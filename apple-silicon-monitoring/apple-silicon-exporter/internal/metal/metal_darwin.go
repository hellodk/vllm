//go:build darwin

// This file provides GPU metrics via Metal using Objective-C through cgo.
package metal

/*
#cgo CFLAGS: -x objective-c
#cgo LDFLAGS: -framework Metal -framework Foundation

#import <Metal/Metal.h>
#import <Foundation/Foundation.h>

typedef struct {
    double utilization;
    uint64_t recommended_working_set_size;
    uint64_t current_allocated_size;
    int has_unified_memory;
    char device_name[256];
} MetalGPUInfo;

int get_metal_gpu_info(MetalGPUInfo* info) {
    @autoreleasepool {
        // Get default Metal device (Apple Silicon has one unified GPU)
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            return -1;
        }

        // Device name
        NSString* name = device.name;
        strncpy(info->device_name, [name UTF8String], 255);
        info->device_name[255] = '\0';

        // Memory info
        info->recommended_working_set_size = device.recommendedMaxWorkingSetSize;
        info->current_allocated_size = device.currentAllocatedSize;
        info->has_unified_memory = device.hasUnifiedMemory ? 1 : 0;

        // Note: Direct GPU utilization isn't available via Metal API
        // We would need to use Metal Performance Counters which require
        // a more complex setup with counter sample buffers
        info->utilization = -1.0;  // Not available

        return 0;
    }
}

// Metal counter sampling for GPU utilization
// This requires creating a command queue and sampling counters
typedef struct {
    double vertex_utilization;
    double fragment_utilization;
    double compute_utilization;
    double total_utilization;
} MetalCounters;

int sample_metal_counters(MetalCounters* counters) {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            return -1;
        }

        // Check if counter sampling is supported
        if (![device supportsCounterSampling:MTLCounterSamplingPointAtStageBoundary]) {
            // Counter sampling not supported
            counters->total_utilization = -1.0;
            return -1;
        }

        // Get counter sets
        NSArray<id<MTLCounterSet>>* counterSets = device.counterSets;
        if (!counterSets || counterSets.count == 0) {
            return -1;
        }

        // Look for GPU utilization counters
        for (id<MTLCounterSet> set in counterSets) {
            if ([set.name containsString:@"GPU"]) {
                for (id<MTLCounter> counter in set.counters) {
                    if ([counter.name containsString:@"Utilization"]) {
                        // Found utilization counter
                        // Would need to set up counter sample buffer to read values
                        // This is complex and requires running actual GPU work
                    }
                }
            }
        }

        // Default: utilization not directly measurable
        counters->total_utilization = -1.0;
        return 0;
    }
}
*/
import "C"

import (
	"go.uber.org/zap"
)

// Collector collects metrics via Metal APIs.
type Collector struct {
	logger *zap.Logger
}

// NewCollector creates a new Metal collector.
func NewCollector(logger *zap.Logger) (*Collector, error) {
	return &Collector{logger: logger}, nil
}

// Collect gathers metrics from Metal APIs.
func (c *Collector) Collect() (*Metrics, error) {
	metrics := &Metrics{
		GPUs: make([]GPUMetrics, 0, 1),
	}

	var gpuInfo C.MetalGPUInfo
	if result := C.get_metal_gpu_info(&gpuInfo); result == 0 {
		gpu := GPUMetrics{
			DeviceName:        C.GoString(&gpuInfo.device_name[0]),
			AllocatedMemory:   uint64(gpuInfo.current_allocated_size),
			RecommendedMemory: uint64(gpuInfo.recommended_working_set_size),
			HasUnifiedMemory:  gpuInfo.has_unified_memory != 0,
			Utilization:       float64(gpuInfo.utilization),
		}

		var counters C.MetalCounters
		if result := C.sample_metal_counters(&counters); result == 0 {
			if counters.total_utilization >= 0 {
				gpu.Utilization = float64(counters.total_utilization)
			}
		}

		metrics.GPUs = append(metrics.GPUs, gpu)
		c.logger.Debug("Metal GPU info collected",
			zap.String("device", gpu.DeviceName),
			zap.Uint64("allocated", gpu.AllocatedMemory),
			zap.Bool("unified_memory", gpu.HasUnifiedMemory),
		)
	} else {
		c.logger.Debug("Failed to get Metal GPU info")
	}

	return metrics, nil
}
