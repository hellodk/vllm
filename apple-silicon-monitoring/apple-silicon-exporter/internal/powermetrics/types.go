// Package powermetrics collects power and performance metrics.
//
// On macOS it parses the output of Apple's `powermetrics` binary (requires
// root). On Linux it derives system power from sysfs where available: hwmon
// power*_input sensors (instantaneous, e.g. on Asahi) or the RAPL powercap
// interface (energy deltas, e.g. on Intel/AMD). Metrics with no Linux source
// (per-cluster CPU power, GPU power, ANE) are reported as unavailable.
package powermetrics

// Metrics contains power and performance metrics. The Has* flags indicate
// which fields carry real data on the current platform.
type Metrics struct {
	SystemPower    float64 // Total system/package power in watts
	GPUPower       float64 // GPU power in watts
	ANEPower       float64 // Neural Engine power in watts
	ANEUtilization float64 // Neural Engine utilization percentage
	ECPUPower      float64 // Efficiency CPU cluster power
	PCPUPower      float64 // Performance CPU cluster power
	CPUFreqE       float64 // Efficiency cluster frequency MHz
	CPUFreqP       float64 // Performance cluster frequency MHz
	GPUFreq        float64 // GPU frequency MHz

	HasSystemPower    bool
	HasGPUPower       bool
	HasANEPower       bool
	HasANEUtilization bool
	HasCPUPower       bool
}
