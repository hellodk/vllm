// Package iokit provides GPU and thermal metrics.
//
// On macOS these come from the IOKit framework via cgo. On Linux they are
// derived from sysfs (hwmon temperatures and the DRM subsystem) where
// available; metrics that have no Linux source are reported as unavailable so
// the collector can omit them gracefully instead of emitting misleading zeros.
package iokit

// GPUMetrics contains metrics for a single GPU. The Has* flags indicate which
// fields carry real data on the current platform.
type GPUMetrics struct {
	Utilization float64
	MemoryUsed  uint64
	MemoryTotal uint64
	Temperature float64

	HasUtilization bool
	HasMemory      bool
	HasTemperature bool
}

// Metrics contains all collected IOKit/sysfs GPU and thermal metrics.
type Metrics struct {
	GPUs []GPUMetrics

	// ThermalPressureLevel is the numeric thermal pressure level: 0=nominal,
	// 1=fair/moderate, 2=serious/heavy, 3=critical. HasThermal is false when
	// no thermal-pressure source is available (e.g. generic Linux); in that
	// case ThermalPressureLevel is 0 but must not be emitted.
	ThermalPressureLevel int
	HasThermal           bool
	CPUThrottled         bool
	GPUThrottled         bool
}
