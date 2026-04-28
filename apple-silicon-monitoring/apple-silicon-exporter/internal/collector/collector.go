// Package collector provides the main Prometheus collector for Apple Silicon metrics
package collector

import (
	"fmt"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"go.uber.org/zap"

	"github.com/company/apple-silicon-exporter/internal/iokit"
	"github.com/company/apple-silicon-exporter/internal/metal"
	"github.com/company/apple-silicon-exporter/internal/powermetrics"
	"github.com/company/apple-silicon-exporter/internal/system"
)

const namespace = "apple"

// AppleSiliconCollector collects metrics from Apple Silicon hardware
type AppleSiliconCollector struct {
	logger *zap.Logger
	mu     sync.Mutex

	// Sub-collectors
	iokitCollector       *iokit.Collector
	powermetricsCollector *powermetrics.Collector
	metalCollector       *metal.Collector
	systemCollector      *system.Collector

	// Configuration
	enableIOKit       bool
	enablePowermetrics bool
	enableMetal       bool

	// Metrics descriptors
	gpuUtilization    *prometheus.Desc
	gpuMemoryUsed     *prometheus.Desc
	gpuMemoryTotal    *prometheus.Desc
	gpuTemperature    *prometheus.Desc
	gpuPower          *prometheus.Desc
	cpuPower          *prometheus.Desc
	anePower          *prometheus.Desc
	aneUtilization    *prometheus.Desc
	systemPower       *prometheus.Desc
	thermalPressure   *prometheus.Desc
	thermalThrottle   *prometheus.Desc
	cpuUsage          *prometheus.Desc
	memoryUsed        *prometheus.Desc
	memoryTotal       *prometheus.Desc
	scrapeSuccess     *prometheus.Desc
	scrapeDuration    *prometheus.Desc
}

// Option configures the collector
type Option func(*AppleSiliconCollector)

// WithIOKit enables or disables IOKit-based collection
func WithIOKit(enabled bool) Option {
	return func(c *AppleSiliconCollector) {
		c.enableIOKit = enabled
	}
}

// WithPowermetrics enables or disables powermetrics-based collection
func WithPowermetrics(enabled bool, path string, samples int, interval time.Duration) Option {
	return func(c *AppleSiliconCollector) {
		c.enablePowermetrics = enabled
		if enabled {
			c.powermetricsCollector = powermetrics.NewCollector(c.logger, path, samples, interval)
		}
	}
}

// WithMetal enables or disables Metal performance counter collection
func WithMetal(enabled bool) Option {
	return func(c *AppleSiliconCollector) {
		c.enableMetal = enabled
	}
}

// NewAppleSiliconCollector creates a new collector
func NewAppleSiliconCollector(logger *zap.Logger, opts ...Option) (*AppleSiliconCollector, error) {
	c := &AppleSiliconCollector{
		logger:            logger,
		enableIOKit:       true,
		enablePowermetrics: true,
		enableMetal:       true,
	}

	// Apply options
	for _, opt := range opts {
		opt(c)
	}

	// Initialize sub-collectors
	var err error

	if c.enableIOKit {
		c.iokitCollector, err = iokit.NewCollector(logger)
		if err != nil {
			logger.Warn("Failed to initialize IOKit collector, continuing without it", zap.Error(err))
			c.enableIOKit = false
		}
	}

	if c.enableMetal {
		c.metalCollector, err = metal.NewCollector(logger)
		if err != nil {
			logger.Warn("Failed to initialize Metal collector, continuing without it", zap.Error(err))
			c.enableMetal = false
		}
	}

	c.systemCollector = system.NewCollector(logger)

	// Initialize metric descriptors
	c.initDescriptors()

	return c, nil
}

func (c *AppleSiliconCollector) initDescriptors() {
	c.gpuUtilization = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "gpu", "utilization_percent"),
		"GPU utilization percentage",
		[]string{"gpu"}, nil,
	)
	c.gpuMemoryUsed = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "gpu", "memory_used_bytes"),
		"GPU memory currently in use",
		[]string{"gpu"}, nil,
	)
	c.gpuMemoryTotal = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "gpu", "memory_total_bytes"),
		"Total GPU memory available",
		[]string{"gpu"}, nil,
	)
	c.gpuTemperature = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "gpu", "temperature_celsius"),
		"GPU temperature in Celsius",
		[]string{"gpu"}, nil,
	)
	c.gpuPower = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "gpu", "power_watts"),
		"GPU power consumption in watts",
		[]string{"gpu"}, nil,
	)
	c.cpuPower = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "cpu", "power_watts"),
		"CPU cluster power consumption in watts",
		[]string{"cluster"}, nil,
	)
	c.anePower = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "ane", "power_watts"),
		"Apple Neural Engine power consumption in watts",
		nil, nil,
	)
	c.aneUtilization = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "ane", "utilization_percent"),
		"Apple Neural Engine utilization percentage",
		nil, nil,
	)
	c.systemPower = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "system", "power_watts"),
		"Total system power consumption in watts",
		nil, nil,
	)
	c.thermalPressure = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "thermal", "pressure"),
		"Thermal pressure level (0=nominal, 1=moderate, 2=heavy, 3=critical)",
		[]string{"level"}, nil,
	)
	c.thermalThrottle = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "thermal", "throttle_active"),
		"Whether thermal throttling is active (1=active, 0=inactive)",
		[]string{"type"}, nil,
	)
	c.cpuUsage = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "cpu", "usage_percent"),
		"CPU usage percentage",
		[]string{"cpu", "mode"}, nil,
	)
	c.memoryUsed = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "memory", "used_bytes"),
		"System memory in use",
		nil, nil,
	)
	c.memoryTotal = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "memory", "total_bytes"),
		"Total system memory",
		nil, nil,
	)
	c.scrapeSuccess = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "scrape", "success"),
		"Whether the scrape was successful",
		[]string{"collector"}, nil,
	)
	c.scrapeDuration = prometheus.NewDesc(
		prometheus.BuildFQName(namespace, "scrape", "duration_seconds"),
		"Duration of the scrape",
		[]string{"collector"}, nil,
	)
}

// Describe implements prometheus.Collector
func (c *AppleSiliconCollector) Describe(ch chan<- *prometheus.Desc) {
	ch <- c.gpuUtilization
	ch <- c.gpuMemoryUsed
	ch <- c.gpuMemoryTotal
	ch <- c.gpuTemperature
	ch <- c.gpuPower
	ch <- c.cpuPower
	ch <- c.anePower
	ch <- c.aneUtilization
	ch <- c.systemPower
	ch <- c.thermalPressure
	ch <- c.thermalThrottle
	ch <- c.cpuUsage
	ch <- c.memoryUsed
	ch <- c.memoryTotal
	ch <- c.scrapeSuccess
	ch <- c.scrapeDuration
}

// Collect implements prometheus.Collector
func (c *AppleSiliconCollector) Collect(ch chan<- prometheus.Metric) {
	c.mu.Lock()
	defer c.mu.Unlock()

	var wg sync.WaitGroup

	// Collect from IOKit
	if c.enableIOKit && c.iokitCollector != nil {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c.collectIOKit(ch)
		}()
	}

	// Collect from powermetrics
	if c.enablePowermetrics && c.powermetricsCollector != nil {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c.collectPowermetrics(ch)
		}()
	}

	// Collect from Metal
	if c.enableMetal && c.metalCollector != nil {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c.collectMetal(ch)
		}()
	}

	// Collect system metrics (always enabled)
	wg.Add(1)
	go func() {
		defer wg.Done()
		c.collectSystem(ch)
	}()

	wg.Wait()
}

func (c *AppleSiliconCollector) collectIOKit(ch chan<- prometheus.Metric) {
	start := time.Now()
	success := 1.0

	metrics, err := c.iokitCollector.Collect()
	if err != nil {
		c.logger.Error("IOKit collection failed", zap.Error(err))
		success = 0.0
	} else {
		// GPU metrics
		for i, gpu := range metrics.GPUs {
			gpuLabel := fmt.Sprintf("%d", i)
			ch <- prometheus.MustNewConstMetric(c.gpuUtilization, prometheus.GaugeValue, gpu.Utilization, gpuLabel)
			ch <- prometheus.MustNewConstMetric(c.gpuMemoryUsed, prometheus.GaugeValue, float64(gpu.MemoryUsed), gpuLabel)
			ch <- prometheus.MustNewConstMetric(c.gpuMemoryTotal, prometheus.GaugeValue, float64(gpu.MemoryTotal), gpuLabel)
			ch <- prometheus.MustNewConstMetric(c.gpuTemperature, prometheus.GaugeValue, gpu.Temperature, gpuLabel)
		}

		// Thermal metrics
		ch <- prometheus.MustNewConstMetric(c.thermalPressure, prometheus.GaugeValue, 1, metrics.ThermalLevel)
		ch <- prometheus.MustNewConstMetric(c.thermalThrottle, prometheus.GaugeValue, boolToFloat(metrics.CPUThrottled), "cpu")
		ch <- prometheus.MustNewConstMetric(c.thermalThrottle, prometheus.GaugeValue, boolToFloat(metrics.GPUThrottled), "gpu")
	}

	ch <- prometheus.MustNewConstMetric(c.scrapeSuccess, prometheus.GaugeValue, success, "iokit")
	ch <- prometheus.MustNewConstMetric(c.scrapeDuration, prometheus.GaugeValue, time.Since(start).Seconds(), "iokit")
}

func (c *AppleSiliconCollector) collectPowermetrics(ch chan<- prometheus.Metric) {
	start := time.Now()
	success := 1.0

	metrics, err := c.powermetricsCollector.Collect()
	if err != nil {
		c.logger.Error("Powermetrics collection failed", zap.Error(err))
		success = 0.0
	} else {
		// Power metrics
		ch <- prometheus.MustNewConstMetric(c.systemPower, prometheus.GaugeValue, metrics.SystemPower)
		ch <- prometheus.MustNewConstMetric(c.gpuPower, prometheus.GaugeValue, metrics.GPUPower, "0")
		ch <- prometheus.MustNewConstMetric(c.anePower, prometheus.GaugeValue, metrics.ANEPower)
		ch <- prometheus.MustNewConstMetric(c.aneUtilization, prometheus.GaugeValue, metrics.ANEUtilization)

		// CPU cluster power
		ch <- prometheus.MustNewConstMetric(c.cpuPower, prometheus.GaugeValue, metrics.ECPUPower, "efficiency")
		ch <- prometheus.MustNewConstMetric(c.cpuPower, prometheus.GaugeValue, metrics.PCPUPower, "performance")
	}

	ch <- prometheus.MustNewConstMetric(c.scrapeSuccess, prometheus.GaugeValue, success, "powermetrics")
	ch <- prometheus.MustNewConstMetric(c.scrapeDuration, prometheus.GaugeValue, time.Since(start).Seconds(), "powermetrics")
}

func (c *AppleSiliconCollector) collectMetal(ch chan<- prometheus.Metric) {
	start := time.Now()
	success := 1.0

	metrics, err := c.metalCollector.Collect()
	if err != nil {
		c.logger.Error("Metal collection failed", zap.Error(err))
		success = 0.0
	} else {
		// Metal provides additional GPU utilization data
		for i, gpu := range metrics.GPUs {
			gpuLabel := fmt.Sprintf("%d", i)
			if gpu.Utilization > 0 {
				ch <- prometheus.MustNewConstMetric(c.gpuUtilization, prometheus.GaugeValue, gpu.Utilization, gpuLabel)
			}
		}
	}

	ch <- prometheus.MustNewConstMetric(c.scrapeSuccess, prometheus.GaugeValue, success, "metal")
	ch <- prometheus.MustNewConstMetric(c.scrapeDuration, prometheus.GaugeValue, time.Since(start).Seconds(), "metal")
}

func (c *AppleSiliconCollector) collectSystem(ch chan<- prometheus.Metric) {
	start := time.Now()
	success := 1.0

	metrics, err := c.systemCollector.Collect()
	if err != nil {
		c.logger.Error("System collection failed", zap.Error(err))
		success = 0.0
	} else {
		// Memory metrics
		ch <- prometheus.MustNewConstMetric(c.memoryUsed, prometheus.GaugeValue, float64(metrics.MemoryUsed))
		ch <- prometheus.MustNewConstMetric(c.memoryTotal, prometheus.GaugeValue, float64(metrics.MemoryTotal))

		// CPU usage per core and mode
		for i, cpu := range metrics.CPUs {
			cpuLabel := fmt.Sprintf("%d", i)
			ch <- prometheus.MustNewConstMetric(c.cpuUsage, prometheus.GaugeValue, cpu.User, cpuLabel, "user")
			ch <- prometheus.MustNewConstMetric(c.cpuUsage, prometheus.GaugeValue, cpu.System, cpuLabel, "system")
			ch <- prometheus.MustNewConstMetric(c.cpuUsage, prometheus.GaugeValue, cpu.Idle, cpuLabel, "idle")
		}
	}

	ch <- prometheus.MustNewConstMetric(c.scrapeSuccess, prometheus.GaugeValue, success, "system")
	ch <- prometheus.MustNewConstMetric(c.scrapeDuration, prometheus.GaugeValue, time.Since(start).Seconds(), "system")
}

func boolToFloat(b bool) float64 {
	if b {
		return 1.0
	}
	return 0.0
}
