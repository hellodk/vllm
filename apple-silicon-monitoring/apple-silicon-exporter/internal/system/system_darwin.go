//go:build darwin

package system

import (
	"bufio"
	"bytes"
	"os/exec"
	"runtime"
	"strconv"
	"strings"

	"go.uber.org/zap"
)

// Collector collects system metrics on macOS.
type Collector struct {
	logger   *zap.Logger
	cpuCount int
}

// NewCollector creates a new system collector.
func NewCollector(logger *zap.Logger) *Collector {
	return &Collector{
		logger:   logger,
		cpuCount: runtime.NumCPU(),
	}
}

// Collect gathers system metrics.
func (c *Collector) Collect() (*Metrics, error) {
	metrics := &Metrics{
		CPUs: make([]CPUMetrics, c.cpuCount),
	}

	if err := c.collectMemory(metrics); err != nil {
		c.logger.Debug("Failed to collect memory metrics", zap.Error(err))
	}
	if err := c.collectCPU(metrics); err != nil {
		c.logger.Debug("Failed to collect CPU metrics", zap.Error(err))
	}
	if err := c.collectLoadAvg(metrics); err != nil {
		c.logger.Debug("Failed to collect load average", zap.Error(err))
	}

	return metrics, nil
}

func (c *Collector) collectMemory(metrics *Metrics) error {
	pageSize, err := sysctlUint64("vm.pagesize")
	if err != nil {
		pageSize = 4096
	}

	totalMem, err := sysctlUint64("hw.memsize")
	if err != nil {
		return err
	}
	metrics.MemoryTotal = totalMem

	output, err := exec.Command("vm_stat").Output()
	if err != nil {
		return err
	}

	var free, active, wired, compressed uint64
	scanner := bufio.NewScanner(bytes.NewReader(output))
	for scanner.Scan() {
		line := scanner.Text()
		switch {
		case strings.HasPrefix(line, "Pages free:"):
			free = parseVMStatLine(line) * pageSize
		case strings.HasPrefix(line, "Pages active:"):
			active = parseVMStatLine(line) * pageSize
		case strings.HasPrefix(line, "Pages wired down:"):
			wired = parseVMStatLine(line) * pageSize
		case strings.HasPrefix(line, "Pages occupied by compressor:"):
			compressed = parseVMStatLine(line) * pageSize
		}
	}

	metrics.MemoryFree = free
	metrics.MemoryUsed = active + wired + compressed

	if output, err := exec.Command("sysctl", "-n", "vm.swapusage").Output(); err == nil {
		parts := strings.Fields(string(output))
		for i, p := range parts {
			if p == "total" && i+2 < len(parts) {
				metrics.SwapTotal = parseSizeWithUnit(parts[i+2])
			} else if p == "used" && i+2 < len(parts) {
				metrics.SwapUsed = parseSizeWithUnit(parts[i+2])
			}
		}
	}

	return nil
}

func (c *Collector) collectCPU(metrics *Metrics) error {
	output, err := exec.Command("sysctl", "-n", "kern.cp_time").Output()
	if err != nil {
		return c.collectCPUFromTop(metrics)
	}

	parts := strings.Fields(string(output))
	if len(parts) >= 5 {
		user, _ := strconv.ParseUint(parts[0], 10, 64)
		nice, _ := strconv.ParseUint(parts[1], 10, 64)
		sys, _ := strconv.ParseUint(parts[2], 10, 64)
		idle, _ := strconv.ParseUint(parts[4], 10, 64)

		total := user + nice + sys + idle
		if total > 0 {
			for i := range metrics.CPUs {
				metrics.CPUs[i].User = float64(user) / float64(total) * 100
				metrics.CPUs[i].Nice = float64(nice) / float64(total) * 100
				metrics.CPUs[i].System = float64(sys) / float64(total) * 100
				metrics.CPUs[i].Idle = float64(idle) / float64(total) * 100
			}
		}
	}

	return nil
}

func (c *Collector) collectCPUFromTop(metrics *Metrics) error {
	output, err := exec.Command("top", "-l", "1", "-n", "0", "-s", "0").Output()
	if err != nil {
		return err
	}

	scanner := bufio.NewScanner(bytes.NewReader(output))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "CPU usage:") {
			line = strings.TrimPrefix(line, "CPU usage:")
			for _, p := range strings.Split(line, ",") {
				p = strings.TrimSpace(p)
				switch {
				case strings.Contains(p, "user"):
					val := parsePercentage(p)
					for i := range metrics.CPUs {
						metrics.CPUs[i].User = val
					}
				case strings.Contains(p, "sys"):
					val := parsePercentage(p)
					for i := range metrics.CPUs {
						metrics.CPUs[i].System = val
					}
				case strings.Contains(p, "idle"):
					val := parsePercentage(p)
					for i := range metrics.CPUs {
						metrics.CPUs[i].Idle = val
					}
				}
			}
			break
		}
	}

	return nil
}

func (c *Collector) collectLoadAvg(metrics *Metrics) error {
	output, err := exec.Command("sysctl", "-n", "vm.loadavg").Output()
	if err != nil {
		return err
	}

	line := strings.Trim(string(output), "{ }\n")
	parts := strings.Fields(line)
	if len(parts) >= 3 {
		metrics.LoadAvg1, _ = strconv.ParseFloat(parts[0], 64)
		metrics.LoadAvg5, _ = strconv.ParseFloat(parts[1], 64)
		metrics.LoadAvg15, _ = strconv.ParseFloat(parts[2], 64)
	}

	return nil
}

func sysctlUint64(name string) (uint64, error) {
	output, err := exec.Command("sysctl", "-n", name).Output()
	if err != nil {
		return 0, err
	}
	return strconv.ParseUint(strings.TrimSpace(string(output)), 10, 64)
}

func parseVMStatLine(line string) uint64 {
	parts := strings.Split(line, ":")
	if len(parts) < 2 {
		return 0
	}
	numStr := strings.TrimSpace(parts[1])
	numStr = strings.TrimSuffix(numStr, ".")
	val, _ := strconv.ParseUint(numStr, 10, 64)
	return val
}

func parseSizeWithUnit(s string) uint64 {
	s = strings.TrimSpace(s)
	var multiplier uint64 = 1

	switch {
	case strings.HasSuffix(s, "G") || strings.HasSuffix(s, "GB"):
		multiplier = 1024 * 1024 * 1024
		s = strings.TrimSuffix(strings.TrimSuffix(s, "GB"), "G")
	case strings.HasSuffix(s, "M") || strings.HasSuffix(s, "MB"):
		multiplier = 1024 * 1024
		s = strings.TrimSuffix(strings.TrimSuffix(s, "MB"), "M")
	case strings.HasSuffix(s, "K") || strings.HasSuffix(s, "KB"):
		multiplier = 1024
		s = strings.TrimSuffix(strings.TrimSuffix(s, "KB"), "K")
	}

	val, _ := strconv.ParseFloat(strings.TrimSpace(s), 64)
	return uint64(val * float64(multiplier))
}

func parsePercentage(s string) float64 {
	parts := strings.Fields(s)
	if len(parts) > 0 {
		numStr := strings.TrimSuffix(parts[0], "%")
		val, _ := strconv.ParseFloat(numStr, 64)
		return val
	}
	return 0
}
