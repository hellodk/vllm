//go:build darwin

// This file provides access to Apple Silicon metrics via the IOKit framework.
// It requires running as root or with appropriate entitlements.
package iokit

/*
#cgo CFLAGS: -x objective-c
#cgo LDFLAGS: -framework IOKit -framework CoreFoundation -framework Foundation

#include <IOKit/IOKitLib.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdint.h>

// GPU metrics from IOAccelerator
typedef struct {
    double utilization;
    uint64_t memory_used;
    uint64_t memory_total;
    double temperature;
} GPUMetrics;

// Thermal state
typedef struct {
    int thermal_level;    // 0=nominal, 1=moderate, 2=heavy, 3=critical, -1=unknown
    int cpu_throttled;    // 1=yes, 0=no
    int gpu_throttled;    // 1=yes, 0=no
} ThermalMetrics;

// Get GPU metrics from IOAccelerator service
// Returns 0 on success, -1 on failure
int get_gpu_metrics(GPUMetrics* metrics) {
    io_iterator_t iterator;
    io_service_t service;
    kern_return_t result;

    // Find IOAccelerator services
    result = IOServiceGetMatchingServices(
        kIOMasterPortDefault,
        IOServiceMatching("IOAccelerator"),
        &iterator
    );

    if (result != KERN_SUCCESS) {
        return -1;
    }

    // Get first GPU (Apple Silicon typically has unified GPU)
    service = IOIteratorNext(iterator);
    if (!service) {
        IOObjectRelease(iterator);
        return -1;
    }

    // Get performance statistics
    CFMutableDictionaryRef props = NULL;
    result = IORegistryEntryCreateCFProperties(
        service,
        &props,
        kCFAllocatorDefault,
        0
    );

    if (result == KERN_SUCCESS && props) {
        // Get utilization from PerformanceStatistics
        CFDictionaryRef perfStats = CFDictionaryGetValue(props, CFSTR("PerformanceStatistics"));
        if (perfStats) {
            CFNumberRef utilRef = CFDictionaryGetValue(perfStats, CFSTR("Device Utilization %"));
            if (utilRef) {
                int64_t util = 0;
                CFNumberGetValue(utilRef, kCFNumberSInt64Type, &util);
                metrics->utilization = (double)util;
            }

            // Memory used
            CFNumberRef memUsedRef = CFDictionaryGetValue(perfStats, CFSTR("In use system memory"));
            if (memUsedRef) {
                CFNumberGetValue(memUsedRef, kCFNumberSInt64Type, (int64_t*)&metrics->memory_used);
            }

            // Allocated memory (as proxy for total available)
            CFNumberRef memAllocRef = CFDictionaryGetValue(perfStats, CFSTR("Allocated system memory"));
            if (memAllocRef) {
                CFNumberGetValue(memAllocRef, kCFNumberSInt64Type, (int64_t*)&metrics->memory_total);
            }
        }
        CFRelease(props);
    }

    IOObjectRelease(service);
    IOObjectRelease(iterator);
    return 0;
}

// Get thermal metrics from AppleSMC
int get_thermal_metrics(ThermalMetrics* metrics) {
    io_service_t service;
    kern_return_t result;

    // Initialize defaults
    metrics->thermal_level = -1;
    metrics->cpu_throttled = 0;
    metrics->gpu_throttled = 0;

    // Find AppleSMC service for thermal data
    service = IOServiceGetMatchingService(
        kIOMasterPortDefault,
        IOServiceMatching("AppleSMC")
    );

    if (!service) {
        // Try alternative service name
        service = IOServiceGetMatchingService(
            kIOMasterPortDefault,
            IOServiceMatching("AppleARMSMC")
        );
    }

    if (!service) {
        return -1;
    }

    CFMutableDictionaryRef props = NULL;
    result = IORegistryEntryCreateCFProperties(
        service,
        &props,
        kCFAllocatorDefault,
        0
    );

    if (result == KERN_SUCCESS && props) {
        // Thermal level might be in various keys depending on macOS version
        // Try common keys
        CFNumberRef thermalRef = CFDictionaryGetValue(props, CFSTR("ThermalLevel"));
        if (thermalRef) {
            int32_t level = 0;
            CFNumberGetValue(thermalRef, kCFNumberSInt32Type, &level);
            metrics->thermal_level = level;
        }
        CFRelease(props);
    }

    IOObjectRelease(service);
    return 0;
}

// Read GPU temperature from SMC (requires root)
double get_gpu_temperature() {
    // SMC key for GPU temperature varies by model
    // Common keys: "TGDD", "Tg0D", "TG0T"
    // This is a simplified implementation - real implementation would
    // need to read SMC keys directly which requires special handling
    return -1.0;  // Return -1 to indicate unavailable
}
*/
import "C"

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"unsafe"

	"go.uber.org/zap"
)

// Collector collects metrics via IOKit.
type Collector struct {
	logger *zap.Logger
}

// NewCollector creates a new IOKit collector.
func NewCollector(logger *zap.Logger) (*Collector, error) {
	return &Collector{logger: logger}, nil
}

// Collect gathers metrics from IOKit.
func (c *Collector) Collect() (*Metrics, error) {
	metrics := &Metrics{
		GPUs:                 make([]GPUMetrics, 0, 1),
		ThermalPressureLevel: 0, // 0 = nominal default
	}

	// Collect GPU metrics
	var gpuMetrics C.GPUMetrics
	if result := C.get_gpu_metrics(&gpuMetrics); result == 0 {
		temp := float64(C.get_gpu_temperature())
		gpu := GPUMetrics{
			Utilization:    float64(gpuMetrics.utilization),
			MemoryUsed:     uint64(gpuMetrics.memory_used),
			MemoryTotal:    uint64(gpuMetrics.memory_total),
			Temperature:    temp,
			HasUtilization: true,
			HasMemory:      true,
			HasTemperature: temp >= 0,
		}
		metrics.GPUs = append(metrics.GPUs, gpu)
	} else {
		c.logger.Debug("Failed to get GPU metrics from IOKit, trying fallback")
		if err := c.collectGPUFallback(metrics); err != nil {
			c.logger.Debug("GPU fallback also failed", zap.Error(err))
		}
	}

	// Collect thermal metrics
	var thermalMetrics C.ThermalMetrics
	if result := C.get_thermal_metrics(&thermalMetrics); result == 0 {
		level := int(thermalMetrics.thermal_level)
		if level < 0 || level > 3 {
			level = 0 // treat unknown as nominal
		}
		metrics.ThermalPressureLevel = level
		metrics.HasThermal = true
		metrics.CPUThrottled = thermalMetrics.cpu_throttled != 0
		metrics.GPUThrottled = thermalMetrics.gpu_throttled != 0
	} else {
		if err := c.collectThermalFallback(metrics); err != nil {
			c.logger.Debug("Thermal fallback also failed", zap.Error(err))
		}
	}

	return metrics, nil
}

// collectGPUFallback tries alternative methods to get GPU data.
func (c *Collector) collectGPUFallback(metrics *Metrics) error {
	cmd := exec.Command("system_profiler", "SPDisplaysDataType", "-json")
	output, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("system_profiler failed: %w", err)
	}

	outputStr := string(output)
	if strings.Contains(outputStr, "Chipset Model") {
		gpu := GPUMetrics{}

		if idx := strings.Index(outputStr, "VRAM"); idx != -1 {
			end := idx + 100
			if end > len(outputStr) {
				end = len(outputStr)
			}
			vramLine := outputStr[idx:end]
			parts := strings.Fields(vramLine)
			for i, p := range parts {
				if p == "GB" && i > 0 {
					if val, err := strconv.ParseFloat(parts[i-1], 64); err == nil {
						gpu.MemoryTotal = uint64(val * 1024 * 1024 * 1024)
						gpu.HasMemory = true
					}
				}
			}
		}

		metrics.GPUs = append(metrics.GPUs, gpu)
	}
	return nil
}

// collectThermalFallback uses sysctl/pmset to get thermal pressure.
func (c *Collector) collectThermalFallback(metrics *Metrics) error {
	// kern.sched_rt_avoid_cpu0 == 1 indicates at least moderate pressure.
	cmd := exec.Command("sysctl", "-n", "kern.sched_rt_avoid_cpu0")
	if output, err := cmd.Output(); err == nil {
		if strings.TrimSpace(string(output)) == "1" {
			metrics.ThermalPressureLevel = 1 // fair/moderate
			metrics.HasThermal = true
		}
	}

	cmd = exec.Command("pmset", "-g", "therm")
	if output, err := cmd.Output(); err == nil {
		outputStr := string(output)
		if strings.Contains(outputStr, "CPU_Speed_Limit") {
			metrics.HasThermal = true
			if strings.Contains(outputStr, "100") {
				metrics.ThermalPressureLevel = 0 // nominal — full speed
			} else {
				metrics.ThermalPressureLevel = 1 // fair — throttled below 100%
				metrics.CPUThrottled = true
			}
		}
	}

	return nil
}

// Ensure unsafe is used (for C interop).
var _ = unsafe.Sizeof(0)
