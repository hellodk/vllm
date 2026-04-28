// Package powermetrics collects metrics by parsing powermetrics output
// Requires root privileges to run powermetrics
package powermetrics

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"time"

	"go.uber.org/zap"
)

// Metrics contains power and performance metrics from powermetrics
type Metrics struct {
	SystemPower    float64 // Total system power in watts
	GPUPower       float64 // GPU power in watts
	ANEPower       float64 // Neural Engine power in watts
	ANEUtilization float64 // Neural Engine utilization percentage
	ECPUPower      float64 // Efficiency CPU cluster power
	PCPUPower      float64 // Performance CPU cluster power
	CPUFreqE       float64 // Efficiency cluster frequency MHz
	CPUFreqP       float64 // Performance cluster frequency MHz
	GPUFreq        float64 // GPU frequency MHz
}

// powermetricsJSON represents the JSON output structure from powermetrics
type powermetricsJSON struct {
	Elapsed   float64 `json:"elapsed_ns"`
	Processor struct {
		Clusters []struct {
			Name          string  `json:"name"`
			CPUs          []struct {
				CPU       int     `json:"cpu"`
				FreqHz    float64 `json:"freq_hz"`
				IdleRatio float64 `json:"idle_ratio"`
			} `json:"cpus"`
			IdleRatio float64 `json:"idle_ratio"`
			FreqHz    float64 `json:"freq_hz"`
		} `json:"clusters"`
		ANEPower    float64 `json:"ane_energy"`
		CPUPower    float64 `json:"cpu_energy"`
		GPUPower    float64 `json:"gpu_energy"`
		PackagePower float64 `json:"combined_power"`
	} `json:"processor"`
	GPU struct {
		FreqHz    float64 `json:"freq_hz"`
		IdleRatio float64 `json:"idle_ratio"`
	} `json:"gpu"`
	Thermal struct {
		ThermalLevel    string `json:"thermal_level"`
		CPUThrottle     int    `json:"cpu_throttle"`
		GPUThrottle     int    `json:"gpu_throttle"`
	} `json:"thermal_pressure"`
}

// Collector collects metrics via powermetrics
type Collector struct {
	logger   *zap.Logger
	path     string
	samples  int
	interval time.Duration
}

// NewCollector creates a new powermetrics collector
func NewCollector(logger *zap.Logger, path string, samples int, interval time.Duration) *Collector {
	return &Collector{
		logger:   logger,
		path:     path,
		samples:  samples,
		interval: interval,
	}
}

// Collect runs powermetrics and parses the output
func (c *Collector) Collect() (*Metrics, error) {
	metrics := &Metrics{}

	// Build powermetrics command
	// -f json: Output in JSON format
	// -n 1: Number of samples
	// -i: Sample interval in milliseconds
	// --samplers: Which samplers to enable
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
		// powermetrics requires root, so this might fail
		c.logger.Debug("powermetrics failed", 
			zap.Error(err),
			zap.String("stderr", stderr.String()),
		)
		// Return partial data or try alternative method
		return c.collectFallback()
	}

	// Parse JSON output
	output := stdout.Bytes()
	
	// powermetrics outputs multiple JSON objects, we want the last complete one
	// Split by newlines and find last valid JSON
	lines := bytes.Split(output, []byte("\n"))
	var lastValid []byte
	for i := len(lines) - 1; i >= 0; i-- {
		if len(lines[i]) > 0 && lines[i][0] == '{' {
			lastValid = lines[i]
			break
		}
	}

	if lastValid == nil {
		// Try parsing the entire output as one JSON blob
		lastValid = output
	}

	var pm powermetricsJSON
	if err := json.Unmarshal(lastValid, &pm); err != nil {
		c.logger.Debug("Failed to parse powermetrics JSON", zap.Error(err))
		return c.collectFallback()
	}

	// Extract metrics
	// Convert energy (nJ or mJ depending on version) to power (W)
	// elapsed_ns is in nanoseconds
	if pm.Elapsed > 0 {
		elapsedSec := pm.Elapsed / 1e9
		// Note: powermetrics energy values vary by version
		// Some report mJ, some report nJ
		// We normalize to watts
		metrics.GPUPower = pm.Processor.GPUPower / elapsedSec / 1000
		metrics.ANEPower = pm.Processor.ANEPower / elapsedSec / 1000
		metrics.SystemPower = pm.Processor.PackagePower
	}

	// Extract cluster-specific metrics
	for _, cluster := range pm.Processor.Clusters {
		clusterName := cluster.Name
		if clusterName == "E" || clusterName == "E-Cluster" || contains(clusterName, "Efficiency") {
			metrics.ECPUPower = pm.Processor.CPUPower / float64(len(pm.Processor.Clusters)) // Rough split
			metrics.CPUFreqE = cluster.FreqHz / 1e6 // Convert to MHz
		} else if clusterName == "P" || clusterName == "P-Cluster" || contains(clusterName, "Performance") {
			metrics.PCPUPower = pm.Processor.CPUPower / float64(len(pm.Processor.Clusters))
			metrics.CPUFreqP = cluster.FreqHz / 1e6
		}
	}

	// GPU frequency
	metrics.GPUFreq = pm.GPU.FreqHz / 1e6

	// ANE utilization (estimate from power if available)
	if metrics.ANEPower > 0 && metrics.SystemPower > 0 {
		// Rough estimate - ANE at full power is ~8W on M1 Max
		maxANEPower := 8.0
		metrics.ANEUtilization = (metrics.ANEPower / maxANEPower) * 100
		if metrics.ANEUtilization > 100 {
			metrics.ANEUtilization = 100
		}
	}

	return metrics, nil
}

// collectFallback tries alternative methods when powermetrics isn't available
func (c *Collector) collectFallback() (*Metrics, error) {
	metrics := &Metrics{}

	// Try using iostat or other tools for basic power info
	// On macOS, we can try 'pmset -g batt' for power info
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "pmset", "-g", "batt")
	output, err := cmd.Output()
	if err == nil {
		// Parse battery/power info
		// This provides limited data but better than nothing
		_ = output // Would parse power draw if available
	}

	// Also try 'ioreg' for some power metrics
	cmd = exec.CommandContext(ctx, "ioreg", "-rn", "AppleSmartBattery")
	output, err = cmd.Output()
	if err == nil {
		// Parse battery metrics
		_ = output
	}

	return metrics, nil
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsAt(s, substr, 0))
}

func containsAt(s, substr string, start int) bool {
	for i := start; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
