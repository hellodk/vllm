//go:build linux

package iokit

import (
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"go.uber.org/zap"
)

// Collector collects GPU/thermal metrics from Linux sysfs.
//
// On Apple Silicon running Asahi Linux, GPU utilisation, GPU memory, and
// thermal-pressure levels are not exposed by the kernel today, so those are
// reported as unavailable. GPU temperature is read from hwmon when a GPU-like
// sensor is present (e.g. on the Asahi DRM driver).
type Collector struct {
	logger *zap.Logger
	root   string // filesystem root, overridable for tests; defaults to "/"
}

// NewCollector creates a new sysfs-backed collector.
func NewCollector(logger *zap.Logger) (*Collector, error) {
	return &Collector{logger: logger, root: "/"}, nil
}

// Collect gathers GPU and thermal metrics from sysfs.
func (c *Collector) Collect() (*Metrics, error) {
	return collectFromSysfs(c.root), nil
}

// collectFromSysfs is the pure collection logic, parameterised by root so it
// can be exercised against fixture trees in tests.
func collectFromSysfs(root string) *Metrics {
	metrics := &Metrics{GPUs: make([]GPUMetrics, 0, 1)}

	if temp, ok := readGPUTemp(filepath.Join(root, "sys", "class", "hwmon")); ok {
		metrics.GPUs = append(metrics.GPUs, GPUMetrics{
			Temperature:    temp,
			HasTemperature: true,
		})
	}

	return metrics
}

// gpuHwmonKeywords identifies hwmon devices that report GPU temperatures.
var gpuHwmonKeywords = []string{"gpu", "agx", "apple"}

// readGPUTemp scans a hwmon directory for a GPU-like sensor and returns its
// temperature in degrees Celsius. hwmon temp*_input values are in millidegrees.
func readGPUTemp(hwmonDir string) (float64, bool) {
	entries, err := os.ReadDir(hwmonDir)
	if err != nil {
		return 0, false
	}

	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)

	for _, name := range names {
		dir := filepath.Join(hwmonDir, name)
		devName := strings.ToLower(readTrimmed(filepath.Join(dir, "name")))
		if !matchesAny(devName, gpuHwmonKeywords) {
			continue
		}
		if v, ok := readMilliDegrees(filepath.Join(dir, "temp1_input")); ok {
			return v, true
		}
	}
	return 0, false
}

func matchesAny(s string, keywords []string) bool {
	for _, k := range keywords {
		if strings.Contains(s, k) {
			return true
		}
	}
	return false
}

func readMilliDegrees(path string) (float64, bool) {
	raw := readTrimmed(path)
	if raw == "" {
		return 0, false
	}
	milli, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return 0, false
	}
	return milli / 1000.0, true
}

func readTrimmed(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}
