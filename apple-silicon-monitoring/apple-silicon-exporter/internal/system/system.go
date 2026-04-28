// Package system provides basic system metrics via syscall/sysctl
// These metrics don't require root privileges
package system

import (
	"bufio"
	"bytes"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"syscall"

	"go.uber.org/zap"
)

// CPUMetrics contains per-CPU usage metrics
type CPUMetrics struct {
	User   float64
	System float64
	Idle   float64
	Nice   float64
}

// Metrics contains system-level metrics
type Metrics struct {
	MemoryUsed  uint64
	MemoryTotal uint64
	MemoryFree  uint64
	SwapUsed    uint64
	SwapTotal   uint64
	CPUs        []CPUMetrics
	LoadAvg1    float64
	LoadAvg5    float64
	LoadAvg15   float64
	Uptime      uint64
}

// Collector collects system metrics
type Collector struct {
	logger    *zap.Logger
	prevCPU   []cpuTimes
	cpuCount  int
}

type cpuTimes struct {
	user   uint64
	system uint64
	idle   uint64
	nice   uint64
}

// NewCollector creates a new system collector
func NewCollector(logger *zap.Logger) *Collector {
	return &Collector{
		logger:   logger,
		cpuCount: runtime.NumCPU(),
		prevCPU:  make([]cpuTimes, runtime.NumCPU()),
	}
}

// Collect gathers system metrics
func (c *Collector) Collect() (*Metrics, error) {
	metrics := &Metrics{
		CPUs: make([]CPUMetrics, c.cpuCount),
	}

	// Collect memory info
	if err := c.collectMemory(metrics); err != nil {
		c.logger.Debug("Failed to collect memory metrics", zap.Error(err))
	}

	// Collect CPU usage
	if err := c.collectCPU(metrics); err != nil {
		c.logger.Debug("Failed to collect CPU metrics", zap.Error(err))
	}

	// Collect load average
	if err := c.collectLoadAvg(metrics); err != nil {
		c.logger.Debug("Failed to collect load average", zap.Error(err))
	}

	return metrics, nil
}

// collectMemory gets memory statistics
func (c *Collector) collectMemory(metrics *Metrics) error {
	// On macOS, use sysctl for memory info
	// vm.page_size, vm.pages, hw.memsize
	
	// Get page size
	pageSize, err := sysctlUint64("vm.pagesize")
	if err != nil {
		pageSize = 4096 // Default
	}

	// Get total memory
	totalMem, err := sysctlUint64("hw.memsize")
	if err != nil {
		return err
	}
	metrics.MemoryTotal = totalMem

	// Use vm_stat for memory breakdown
	cmd := exec.Command("vm_stat")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	var free, active, inactive, wired, compressed, purgeable uint64
	
	scanner := bufio.NewScanner(bytes.NewReader(output))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "Pages free:") {
			free = parseVMStatLine(line) * pageSize
		} else if strings.HasPrefix(line, "Pages active:") {
			active = parseVMStatLine(line) * pageSize
		} else if strings.HasPrefix(line, "Pages inactive:") {
			inactive = parseVMStatLine(line) * pageSize
		} else if strings.HasPrefix(line, "Pages wired down:") {
			wired = parseVMStatLine(line) * pageSize
		} else if strings.HasPrefix(line, "Pages occupied by compressor:") {
			compressed = parseVMStatLine(line) * pageSize
		} else if strings.HasPrefix(line, "Pages purgeable:") {
			purgeable = parseVMStatLine(line) * pageSize
		}
	}

	metrics.MemoryFree = free
	metrics.MemoryUsed = active + wired + compressed
	_ = inactive  // Inactive can be reclaimed
	_ = purgeable // Purgeable can be freed

	// Swap info
	cmd = exec.Command("sysctl", "-n", "vm.swapusage")
	output, err = cmd.Output()
	if err == nil {
		// Parse: "total = 2048.00M  used = 512.00M  free = 1536.00M"
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

// collectCPU gets CPU usage statistics
func (c *Collector) collectCPU(metrics *Metrics) error {
	// Use top in batch mode for CPU stats
	// Or use host_processor_info() via cgo for accurate per-CPU stats
	
	// Simplified: use overall CPU from sysctl
	cmd := exec.Command("sysctl", "-n", "kern.cp_time")
	output, err := cmd.Output()
	if err != nil {
		// Fallback to top
		return c.collectCPUFromTop(metrics)
	}

	// Parse kern.cp_time output
	parts := strings.Fields(string(output))
	if len(parts) >= 5 {
		user, _ := strconv.ParseUint(parts[0], 10, 64)
		nice, _ := strconv.ParseUint(parts[1], 10, 64)
		sys, _ := strconv.ParseUint(parts[2], 10, 64)
		idle, _ := strconv.ParseUint(parts[4], 10, 64)
		
		total := user + nice + sys + idle
		if total > 0 {
			// Set same values for all CPUs (aggregate)
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

// collectCPUFromTop uses top command as fallback
func (c *Collector) collectCPUFromTop(metrics *Metrics) error {
	cmd := exec.Command("top", "-l", "1", "-n", "0", "-s", "0")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	scanner := bufio.NewScanner(bytes.NewReader(output))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "CPU usage:") {
			// Parse: "CPU usage: 10.0% user, 5.0% sys, 85.0% idle"
			line = strings.TrimPrefix(line, "CPU usage:")
			parts := strings.Split(line, ",")
			for _, p := range parts {
				p = strings.TrimSpace(p)
				if strings.Contains(p, "user") {
					val := parsePercentage(p)
					for i := range metrics.CPUs {
						metrics.CPUs[i].User = val
					}
				} else if strings.Contains(p, "sys") {
					val := parsePercentage(p)
					for i := range metrics.CPUs {
						metrics.CPUs[i].System = val
					}
				} else if strings.Contains(p, "idle") {
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

// collectLoadAvg gets system load averages
func (c *Collector) collectLoadAvg(metrics *Metrics) error {
	var info syscall.Sysinfo_t
	// Note: syscall.Sysinfo doesn't exist on macOS, use sysctl instead
	
	cmd := exec.Command("sysctl", "-n", "vm.loadavg")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	// Parse: "{ 1.50 2.00 3.00 }"
	line := strings.Trim(string(output), "{ }\n")
	parts := strings.Fields(line)
	if len(parts) >= 3 {
		metrics.LoadAvg1, _ = strconv.ParseFloat(parts[0], 64)
		metrics.LoadAvg5, _ = strconv.ParseFloat(parts[1], 64)
		metrics.LoadAvg15, _ = strconv.ParseFloat(parts[2], 64)
	}

	_ = info // Silence unused variable
	return nil
}

// Helper functions

func sysctlUint64(name string) (uint64, error) {
	cmd := exec.Command("sysctl", "-n", name)
	output, err := cmd.Output()
	if err != nil {
		return 0, err
	}
	return strconv.ParseUint(strings.TrimSpace(string(output)), 10, 64)
}

func parseVMStatLine(line string) uint64 {
	// "Pages free:                            12345."
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
	
	if strings.HasSuffix(s, "G") || strings.HasSuffix(s, "GB") {
		multiplier = 1024 * 1024 * 1024
		s = strings.TrimSuffix(strings.TrimSuffix(s, "GB"), "G")
	} else if strings.HasSuffix(s, "M") || strings.HasSuffix(s, "MB") {
		multiplier = 1024 * 1024
		s = strings.TrimSuffix(strings.TrimSuffix(s, "MB"), "M")
	} else if strings.HasSuffix(s, "K") || strings.HasSuffix(s, "KB") {
		multiplier = 1024
		s = strings.TrimSuffix(strings.TrimSuffix(s, "KB"), "K")
	}
	
	val, _ := strconv.ParseFloat(strings.TrimSpace(s), 64)
	return uint64(val * float64(multiplier))
}

func parsePercentage(s string) float64 {
	// "10.0% user" -> 10.0
	parts := strings.Fields(s)
	if len(parts) > 0 {
		numStr := strings.TrimSuffix(parts[0], "%")
		val, _ := strconv.ParseFloat(numStr, 64)
		return val
	}
	return 0
}
