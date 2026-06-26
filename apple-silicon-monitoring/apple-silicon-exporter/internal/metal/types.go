// Package metal provides GPU metrics via the Metal framework on macOS.
//
// Metal is a macOS-only API. On Linux NewCollector returns an error and the
// parent collector degrades gracefully by disabling Metal collection.
package metal

// GPUMetrics contains Metal GPU metrics.
type GPUMetrics struct {
	Utilization       float64
	AllocatedMemory   uint64
	RecommendedMemory uint64
	DeviceName        string
	HasUnifiedMemory  bool
}

// Metrics contains all Metal-collected metrics.
type Metrics struct {
	GPUs []GPUMetrics
}
