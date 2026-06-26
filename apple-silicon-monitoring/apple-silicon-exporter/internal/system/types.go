// Package system provides basic CPU and memory metrics.
//
// These do not require root. On macOS they come from sysctl/vm_stat; on Linux
// from /proc (stat, meminfo, loadavg).
package system

// CPUMetrics contains per-CPU usage percentages by mode.
type CPUMetrics struct {
	User   float64
	System float64
	Idle   float64
	Nice   float64
}

// Metrics contains system-level metrics.
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
