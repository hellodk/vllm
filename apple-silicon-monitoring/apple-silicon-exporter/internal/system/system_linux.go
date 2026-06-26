//go:build linux

package system

import (
	"bufio"
	"bytes"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"

	"go.uber.org/zap"
)

// Collector collects system metrics from /proc on Linux.
type Collector struct {
	logger *zap.Logger
	root   string // filesystem root, overridable for tests; defaults to "/"

	mu      sync.Mutex
	prevCPU []cpuTimes
}

type cpuTimes struct {
	user, nice, system, idle, iowait, irq, softirq, steal uint64
}

func (t cpuTimes) total() uint64 {
	return t.user + t.nice + t.system + t.idle + t.iowait + t.irq + t.softirq + t.steal
}

// NewCollector creates a new /proc-backed collector.
func NewCollector(logger *zap.Logger) *Collector {
	return &Collector{logger: logger, root: "/"}
}

// Collect gathers system metrics from /proc.
func (c *Collector) Collect() (*Metrics, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	metrics := &Metrics{}

	if data, err := os.ReadFile(filepath.Join(c.root, "proc", "meminfo")); err == nil {
		total, used, free := parseMemInfo(data)
		metrics.MemoryTotal = total
		metrics.MemoryUsed = used
		metrics.MemoryFree = free
	} else {
		c.logger.Debug("Failed to read /proc/meminfo", zap.Error(err))
	}

	if data, err := os.ReadFile(filepath.Join(c.root, "proc", "stat")); err == nil {
		cur := parseProcStat(data)
		metrics.CPUs = make([]CPUMetrics, len(cur))
		for i, t := range cur {
			var prev cpuTimes
			if i < len(c.prevCPU) {
				prev = c.prevCPU[i]
			}
			metrics.CPUs[i] = cpuPercent(prev, t)
		}
		c.prevCPU = cur
	} else {
		c.logger.Debug("Failed to read /proc/stat", zap.Error(err))
	}

	if data, err := os.ReadFile(filepath.Join(c.root, "proc", "loadavg")); err == nil {
		metrics.LoadAvg1, metrics.LoadAvg5, metrics.LoadAvg15 = parseLoadAvg(data)
	}

	return metrics, nil
}

// parseMemInfo parses /proc/meminfo and returns total, used and free bytes.
// "used" prefers MemAvailable (total-available) and falls back to the classic
// total-free-buffers-cached calculation.
func parseMemInfo(data []byte) (total, used, free uint64) {
	var memTotal, memFree, memAvailable, buffers, cached, sReclaimable uint64
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 2 {
			continue
		}
		// values are in kB
		v, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil {
			continue
		}
		v *= 1024
		switch strings.TrimSuffix(fields[0], ":") {
		case "MemTotal":
			memTotal = v
		case "MemFree":
			memFree = v
		case "MemAvailable":
			memAvailable = v
		case "Buffers":
			buffers = v
		case "Cached":
			cached = v
		case "SReclaimable":
			sReclaimable = v
		}
	}

	total = memTotal
	free = memFree
	switch {
	case memAvailable > 0 && memTotal >= memAvailable:
		used = memTotal - memAvailable
	case memTotal >= memFree+buffers+cached+sReclaimable:
		used = memTotal - memFree - buffers - cached - sReclaimable
	}
	return total, used, free
}

// parseProcStat parses per-CPU lines (cpu0, cpu1, ...) from /proc/stat into
// cumulative jiffy counters. The aggregate "cpu" line is skipped.
func parseProcStat(data []byte) []cpuTimes {
	var out []cpuTimes
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 5 || !strings.HasPrefix(fields[0], "cpu") {
			continue
		}
		// Skip the aggregate "cpu" line (no trailing digit).
		if fields[0] == "cpu" {
			continue
		}
		vals := make([]uint64, 0, 8)
		for _, f := range fields[1:] {
			v, _ := strconv.ParseUint(f, 10, 64)
			vals = append(vals, v)
		}
		t := cpuTimes{}
		assign := []*uint64{&t.user, &t.nice, &t.system, &t.idle, &t.iowait, &t.irq, &t.softirq, &t.steal}
		for i := 0; i < len(assign) && i < len(vals); i++ {
			*assign[i] = vals[i]
		}
		out = append(out, t)
	}
	return out
}

// cpuPercent converts two cumulative samples into usage percentages. When the
// delta is zero (e.g. the first sample with no prior state) it derives the
// percentages from the cumulative since-boot totals instead.
func cpuPercent(prev, cur cpuTimes) CPUMetrics {
	deltaTotal := float64(cur.total()) - float64(prev.total())
	du := float64(cur.user) - float64(prev.user)
	dn := float64(cur.nice) - float64(prev.nice)
	ds := float64(cur.system) - float64(prev.system)
	di := float64(cur.idle) - float64(prev.idle) + float64(cur.iowait) - float64(prev.iowait)

	if deltaTotal <= 0 {
		// No usable delta: fall back to since-boot ratios.
		total := float64(cur.total())
		if total <= 0 {
			return CPUMetrics{}
		}
		return CPUMetrics{
			User:   float64(cur.user) / total * 100,
			Nice:   float64(cur.nice) / total * 100,
			System: float64(cur.system) / total * 100,
			Idle:   (float64(cur.idle) + float64(cur.iowait)) / total * 100,
		}
	}

	return CPUMetrics{
		User:   du / deltaTotal * 100,
		Nice:   dn / deltaTotal * 100,
		System: ds / deltaTotal * 100,
		Idle:   di / deltaTotal * 100,
	}
}

func parseLoadAvg(data []byte) (l1, l5, l15 float64) {
	fields := strings.Fields(string(data))
	if len(fields) >= 3 {
		l1, _ = strconv.ParseFloat(fields[0], 64)
		l5, _ = strconv.ParseFloat(fields[1], 64)
		l15, _ = strconv.ParseFloat(fields[2], 64)
	}
	return l1, l5, l15
}
