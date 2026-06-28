//go:build darwin

package powermetrics

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"
)

// powermetricsJSON represents the JSON output structure from powermetrics.
type powermetricsJSON struct {
	Elapsed   float64 `json:"elapsed_ns"`
	Processor struct {
		Clusters []struct {
			Name string `json:"name"`
			CPUs []struct {
				CPU       int     `json:"cpu"`
				FreqHz    float64 `json:"freq_hz"`
				IdleRatio float64 `json:"idle_ratio"`
			} `json:"cpus"`
			IdleRatio float64 `json:"idle_ratio"`
			FreqHz    float64 `json:"freq_hz"`
		} `json:"clusters"`
		ANEPower     float64 `json:"ane_energy"`
		CPUPower     float64 `json:"cpu_energy"`
		GPUPower     float64 `json:"gpu_energy"`
		PackagePower float64 `json:"combined_power"`
	} `json:"processor"`
	GPU struct {
		FreqHz    float64 `json:"freq_hz"`
		IdleRatio float64 `json:"idle_ratio"`
	} `json:"gpu"`
	Thermal struct {
		ThermalLevel string `json:"thermal_level"`
		CPUThrottle  int    `json:"cpu_throttle"`
		GPUThrottle  int    `json:"gpu_throttle"`
	} `json:"thermal_pressure"`
}

// Collector collects metrics via powermetrics and caches results to avoid
// spawning a root subprocess on every Prometheus scrape.
type Collector struct {
	logger   *zap.Logger
	path     string
	samples  int
	interval time.Duration

	// Cache protects against repeated subprocess spawns when multiple
	// scrapers are active or when scrape intervals are short.
	mu        sync.Mutex
	cached    *Metrics
	cacheTime time.Time
	cacheTTL  time.Duration
}

// NewCollector creates a new powermetrics collector.
func NewCollector(logger *zap.Logger, path string, samples int, interval time.Duration) *Collector {
	// TTL = time one powermetrics call takes + a small buffer, minimum 5 s.
	ttl := interval*time.Duration(samples) + 2*time.Second
	if ttl < 5*time.Second {
		ttl = 5 * time.Second
	}
	return &Collector{
		logger:   logger,
		path:     path,
		samples:  samples,
		interval: interval,
		cacheTTL: ttl,
	}
}

// Collect returns cached power metrics when fresh; otherwise calls
// powermetrics, updates the cache, and returns the result. On subprocess
// failure a stale cache entry is returned (if available) to avoid metric gaps.
func (c *Collector) Collect() (*Metrics, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.cached != nil && time.Since(c.cacheTime) < c.cacheTTL {
		m := *c.cached
		return &m, nil
	}

	metrics, err := c.runPowermetrics()
	if err != nil {
		if c.cached != nil {
			c.logger.Warn("powermetrics failed; serving stale cache",
				zap.Error(err),
				zap.Duration("cache_age", time.Since(c.cacheTime)))
			m := *c.cached
			return &m, nil
		}
		// No cache at all — try alternative collection methods.
		return c.collectFallback()
	}

	c.cached = metrics
	c.cacheTime = time.Now()
	return metrics, nil
}

// runPowermetrics executes powermetrics once and parses the JSON output.
func (c *Collector) runPowermetrics() (*Metrics, error) {
	metrics := &Metrics{}

	// Cap subprocess runtime to a generous multiple of the sample interval.
	timeout := c.interval*time.Duration(c.samples)*2 + 5*time.Second
	if timeout < 10*time.Second {
		timeout = 10 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	intervalMs := int(c.interval.Milliseconds())
	if intervalMs < 100 {
		intervalMs = 100
	}

	cmd := exec.CommandContext(ctx, c.path,
		"-f", "json",
		"-n", fmt.Sprintf("%d", c.samples),
		"-i", fmt.Sprintf("%d", intervalMs),
		"--samplers", "cpu_power,gpu_power,ane_power,thermal",
	)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		c.logger.Debug("powermetrics subprocess failed",
			zap.Error(err),
			zap.String("stderr", stderr.String()),
		)
		return nil, fmt.Errorf("powermetrics: %w", err)
	}

	output := stdout.Bytes()
	if len(output) == 0 {
		return nil, fmt.Errorf("powermetrics: empty output")
	}

	// powermetrics emits one JSON object per sample; use the last complete one.
	lines := bytes.Split(output, []byte("\n"))
	var lastValid []byte
	for i := len(lines) - 1; i >= 0; i-- {
		if len(lines[i]) > 0 && lines[i][0] == '{' {
			lastValid = lines[i]
			break
		}
	}
	if lastValid == nil {
		lastValid = output
	}

	var pm powermetricsJSON
	if err := json.Unmarshal(lastValid, &pm); err != nil {
		return nil, fmt.Errorf("powermetrics: JSON parse: %w", err)
	}

	if pm.Elapsed > 0 {
		elapsedSec := pm.Elapsed / 1e9
		metrics.GPUPower = pm.Processor.GPUPower / elapsedSec / 1000
		metrics.ANEPower = pm.Processor.ANEPower / elapsedSec / 1000
		metrics.SystemPower = pm.Processor.PackagePower
		metrics.HasGPUPower = true
		metrics.HasANEPower = true
		metrics.HasSystemPower = true
	}

	for _, cluster := range pm.Processor.Clusters {
		clusterName := cluster.Name
		if clusterName == "E" || clusterName == "E-Cluster" || strings.Contains(clusterName, "Efficiency") {
			metrics.ECPUPower = pm.Processor.CPUPower / float64(len(pm.Processor.Clusters))
			metrics.CPUFreqE = cluster.FreqHz / 1e6
			metrics.HasCPUPower = true
		} else if clusterName == "P" || clusterName == "P-Cluster" || strings.Contains(clusterName, "Performance") {
			metrics.PCPUPower = pm.Processor.CPUPower / float64(len(pm.Processor.Clusters))
			metrics.CPUFreqP = cluster.FreqHz / 1e6
			metrics.HasCPUPower = true
		}
	}

	metrics.GPUFreq = pm.GPU.FreqHz / 1e6

	if metrics.ANEPower > 0 && metrics.SystemPower > 0 {
		const maxANEPower = 8.0 // ~8W at full power on M1 Max class parts
		metrics.ANEUtilization = (metrics.ANEPower / maxANEPower) * 100
		if metrics.ANEUtilization > 100 {
			metrics.ANEUtilization = 100
		}
		metrics.HasANEUtilization = true
	}

	return metrics, nil
}

// collectFallback tries alternative methods when powermetrics is unavailable
// (e.g. not running as root, binary absent). Returns empty metrics (no Has*
// flags set) rather than an error so the exporter stays up.
func (c *Collector) collectFallback() (*Metrics, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if output, err := exec.CommandContext(ctx, "pmset", "-g", "batt").Output(); err == nil {
		_ = output
	}
	if output, err := exec.CommandContext(ctx, "ioreg", "-rn", "AppleSmartBattery").Output(); err == nil {
		_ = output
	}

	return &Metrics{}, nil
}
