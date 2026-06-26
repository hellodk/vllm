//go:build linux

package powermetrics

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestReadHwmonPower(t *testing.T) {
	root := t.TempDir()
	hwmon := filepath.Join(root, "sys", "class", "hwmon")
	writeFile(t, filepath.Join(hwmon, "hwmon0", "name"), "apple_soc\n")
	writeFile(t, filepath.Join(hwmon, "hwmon0", "power1_input"), "5000000\n") // 5 W
	writeFile(t, filepath.Join(hwmon, "hwmon0", "power2_input"), "2500000\n") // 2.5 W
	// A temp sensor that must be ignored.
	writeFile(t, filepath.Join(hwmon, "hwmon0", "temp1_input"), "47000\n")

	watts, ok := readHwmonPower(hwmon)
	if !ok {
		t.Fatal("expected power readings")
	}
	if watts != 7.5 {
		t.Fatalf("watts = %v, want 7.5", watts)
	}
}

func TestReadHwmonPowerNone(t *testing.T) {
	root := t.TempDir()
	hwmon := filepath.Join(root, "sys", "class", "hwmon")
	writeFile(t, filepath.Join(hwmon, "hwmon0", "temp1_input"), "47000\n")
	if _, ok := readHwmonPower(hwmon); ok {
		t.Fatal("expected no power readings")
	}
}

func TestReadRAPLEnergy(t *testing.T) {
	root := t.TempDir()
	pc := filepath.Join(root, "sys", "class", "powercap")
	writeFile(t, filepath.Join(pc, "intel-rapl:0", "energy_uj"), "123456789\n")

	uj, ok := readRAPLEnergy(pc)
	if !ok || uj != 123456789 {
		t.Fatalf("readRAPLEnergy = (%d,%v), want (123456789,true)", uj, ok)
	}
}

func TestComputePowerFromEnergy(t *testing.T) {
	tests := []struct {
		name      string
		prev, cur uint64
		dt        time.Duration
		want      float64
		wantOK    bool
	}{
		{"one watt", 0, 1_000_000, time.Second, 1.0, true},
		{"ten watts", 1_000_000, 11_000_000, time.Second, 10.0, true},
		{"half second", 0, 5_000_000, 500 * time.Millisecond, 10.0, true},
		{"zero interval", 0, 1_000_000, 0, 0, false},
		{"counter wrap", 10_000_000, 5_000, time.Second, 0, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := computePowerFromEnergy(tt.prev, tt.cur, tt.dt)
			if ok != tt.wantOK || got != tt.want {
				t.Fatalf("computePowerFromEnergy = (%v,%v), want (%v,%v)", got, ok, tt.want, tt.wantOK)
			}
		})
	}
}

func TestCollectRAPLDelta(t *testing.T) {
	root := t.TempDir()
	pc := filepath.Join(root, "sys", "class", "powercap")
	energy := filepath.Join(pc, "intel-rapl:0", "energy_uj")
	writeFile(t, energy, "0\n")

	base := time.Unix(1000, 0)
	c := &Collector{root: root}
	cur := base
	c.nowFunc = func() time.Time { return cur }

	// First sample primes the delta; no power yet.
	if _, err := c.Collect(); err == nil {
		t.Fatal("expected first RAPL sample to report no data yet")
	}

	// Second sample one second later with +10 J of energy → 10 W.
	writeFile(t, energy, "10000000\n")
	cur = base.Add(time.Second)
	m, err := c.Collect()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !m.HasSystemPower || m.SystemPower != 10.0 {
		t.Fatalf("SystemPower = %v (has=%v), want 10", m.SystemPower, m.HasSystemPower)
	}
}
