//go:build linux

package iokit

import (
	"os"
	"path/filepath"
	"testing"
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

func TestReadGPUTemp(t *testing.T) {
	root := t.TempDir()
	hwmon := filepath.Join(root, "sys", "class", "hwmon")

	// A non-GPU sensor that must be ignored.
	writeFile(t, filepath.Join(hwmon, "hwmon0", "name"), "coretemp\n")
	writeFile(t, filepath.Join(hwmon, "hwmon0", "temp1_input"), "55000\n")
	// A GPU sensor that should be selected.
	writeFile(t, filepath.Join(hwmon, "hwmon1", "name"), "agx-gpu\n")
	writeFile(t, filepath.Join(hwmon, "hwmon1", "temp1_input"), "47500\n")

	temp, ok := readGPUTemp(hwmon)
	if !ok {
		t.Fatal("expected to find a GPU temperature")
	}
	if temp != 47.5 {
		t.Fatalf("temp = %v, want 47.5", temp)
	}
}

func TestCollectFromSysfsNoGPU(t *testing.T) {
	root := t.TempDir()
	// hwmon dir exists but only has a CPU sensor.
	hwmon := filepath.Join(root, "sys", "class", "hwmon")
	writeFile(t, filepath.Join(hwmon, "hwmon0", "name"), "coretemp\n")
	writeFile(t, filepath.Join(hwmon, "hwmon0", "temp1_input"), "55000\n")

	m := collectFromSysfs(root)
	if len(m.GPUs) != 0 {
		t.Fatalf("expected no GPU metrics, got %d", len(m.GPUs))
	}
	if m.HasThermal {
		t.Fatal("thermal pressure should be unavailable on generic Linux")
	}
}

func TestThermalPressureLevelDefaultsToZero(t *testing.T) {
	root := t.TempDir()
	m := collectFromSysfs(root)
	if m.HasThermal {
		t.Fatal("HasThermal must be false on generic Linux (no sysfs thermal source)")
	}
	if m.ThermalPressureLevel != 0 {
		t.Fatalf("ThermalPressureLevel = %d, want 0 (nominal default)", m.ThermalPressureLevel)
	}
}
