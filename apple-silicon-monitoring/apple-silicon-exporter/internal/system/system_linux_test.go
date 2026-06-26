//go:build linux

package system

import (
	"math"
	"testing"
)

func TestParseMemInfo(t *testing.T) {
	data := []byte(`MemTotal:       16384000 kB
MemFree:         1024000 kB
MemAvailable:    8192000 kB
Buffers:          512000 kB
Cached:          2048000 kB
SReclaimable:     256000 kB
`)
	total, used, free := parseMemInfo(data)
	if total != 16384000*1024 {
		t.Fatalf("total = %d", total)
	}
	if free != 1024000*1024 {
		t.Fatalf("free = %d", free)
	}
	// used prefers MemAvailable: total - available
	if used != (16384000-8192000)*1024 {
		t.Fatalf("used = %d, want %d", used, (16384000-8192000)*1024)
	}
}

func TestParseMemInfoNoAvailable(t *testing.T) {
	data := []byte(`MemTotal:       1000 kB
MemFree:          200 kB
Buffers:          100 kB
Cached:           300 kB
SReclaimable:      50 kB
`)
	_, used, _ := parseMemInfo(data)
	want := uint64((1000 - 200 - 100 - 300 - 50) * 1024)
	if used != want {
		t.Fatalf("used = %d, want %d", used, want)
	}
}

func TestParseProcStat(t *testing.T) {
	data := []byte(`cpu  100 0 100 800 0 0 0 0 0 0
cpu0 50 0 50 400 0 0 0 0 0 0
cpu1 50 0 50 400 0 0 0 0 0 0
intr 12345
`)
	cpus := parseProcStat(data)
	if len(cpus) != 2 {
		t.Fatalf("expected 2 cpus, got %d", len(cpus))
	}
	if cpus[0].user != 50 || cpus[0].idle != 400 {
		t.Fatalf("cpu0 parsed wrong: %+v", cpus[0])
	}
}

func TestCPUPercentDelta(t *testing.T) {
	prev := cpuTimes{user: 100, system: 100, idle: 800}
	// +50 user, +50 system, +400 idle => total delta 500
	cur := cpuTimes{user: 150, system: 150, idle: 1200}
	m := cpuPercent(prev, cur)
	if !approx(m.User, 10) || !approx(m.System, 10) || !approx(m.Idle, 80) {
		t.Fatalf("unexpected percentages: %+v", m)
	}
}

func TestCPUPercentFirstSample(t *testing.T) {
	// No prior delta: fall back to since-boot ratios.
	cur := cpuTimes{user: 100, system: 100, idle: 800}
	m := cpuPercent(cpuTimes{}, cur)
	if !approx(m.User, 10) || !approx(m.Idle, 80) {
		t.Fatalf("unexpected since-boot percentages: %+v", m)
	}
}

func TestParseLoadAvg(t *testing.T) {
	l1, l5, l15 := parseLoadAvg([]byte("0.50 1.00 2.00 1/234 5678\n"))
	if !approx(l1, 0.5) || !approx(l5, 1.0) || !approx(l15, 2.0) {
		t.Fatalf("loadavg = %v %v %v", l1, l5, l15)
	}
}

func approx(a, b float64) bool {
	return math.Abs(a-b) < 1e-6
}
