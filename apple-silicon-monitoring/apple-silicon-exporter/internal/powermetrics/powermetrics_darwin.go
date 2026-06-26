//go:build darwin

package powermetrics

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
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

// Collector collects metrics via powermetrics.
type Collector struct {
	logger   *zap.Logger
	path     string
	samples  int
	interval time.Duration
}

// NewCollector creates a new powermetrics collector.
func NewCollector(logger *zap.Logger, path string, samples int, interval time.Duration) *Collector {
	return &Collector{
		logger:   logger,
		path:     path,
		samples:  samples,
		interval: interval,
	}
}

// Collect runs powermetrics and parses the output.
func (c *Collector) Collect() (*Metrics, error) {
	metrics := &Metrics{}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
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
		c.logger.Debug("powermetrics failed",
			zap.Error(err),
			zap.String("stderr", stderr.String()),
		)
		return c.collectFallback()
	}

	output := stdout.Bytes()

	// powermetrics outputs multiple JSON objects; find the last complete one.
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
		c.logger.Debug("Failed to parse powermetrics JSON", zap.Error(err))
		return c.collectFallback()
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

// collectFallback tries alternative methods when powermetrics isn't available.
func (c *Collector) collectFallback() (*Metrics, error) {
	metrics := &Metrics{}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if output, err := exec.CommandContext(ctx, "pmset", "-g", "batt").Output(); err == nil {
		_ = output // limited battery/power info; not currently parsed
	}
	if output, err := exec.CommandContext(ctx, "ioreg", "-rn", "AppleSmartBattery").Output(); err == nil {
		_ = output
	}

	return metrics, nil
}
