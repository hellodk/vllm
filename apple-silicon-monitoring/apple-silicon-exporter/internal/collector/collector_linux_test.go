//go:build linux

package collector

import (
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"go.uber.org/zap"
)

// TestThermalPressureDescriptorNoLabels verifies that apple_thermal_pressure is
// registered as a label-free numeric gauge (the SRE-6 / alerting contract).
func TestThermalPressureDescriptorNoLabels(t *testing.T) {
	logger := zap.NewNop()
	c, err := NewAppleSiliconCollector(logger,
		WithIOKit(false),
		WithPowermetrics(false, "/nonexistent", 1, 0),
		WithMetal(false),
	)
	if err != nil {
		t.Fatalf("NewAppleSiliconCollector: %v", err)
	}

	ch := make(chan *prometheus.Desc, 32)
	c.Describe(ch)
	close(ch)

	for desc := range ch {
		s := desc.String()
		if !strings.Contains(s, "apple_thermal_pressure") {
			continue
		}
		// The Desc string representation contains "variableLabels: []" when
		// there are no variable labels. Confirm the level label is absent.
		if strings.Contains(s, `"level"`) {
			t.Errorf("apple_thermal_pressure descriptor must not carry a 'level' label; got: %s", s)
		}
	}
}

// TestCollectorInitLinux verifies that the collector can be created and
// gathered from on Linux with all platform-specific sub-collectors disabled.
func TestCollectorInitLinux(t *testing.T) {
	logger := zap.NewNop()
	c, err := NewAppleSiliconCollector(logger,
		WithIOKit(false),
		WithPowermetrics(false, "/nonexistent", 1, 0),
		WithMetal(false),
	)
	if err != nil {
		t.Fatalf("NewAppleSiliconCollector: %v", err)
	}

	reg := prometheus.NewRegistry()
	if err := reg.Register(c); err != nil {
		t.Fatalf("Register: %v", err)
	}

	mfs, err := reg.Gather()
	if err != nil {
		t.Fatalf("Gather: %v", err)
	}

	// apple_thermal_pressure must NOT appear when HasThermal is false (Linux).
	for _, mf := range mfs {
		if mf.GetName() == "apple_thermal_pressure" {
			t.Error("apple_thermal_pressure must not be emitted when HasThermal=false (Linux)")
		}
	}

	// apple_chip_info must always be present.
	found := false
	for _, mf := range mfs {
		if mf.GetName() == "apple_chip_info" {
			found = true
			break
		}
	}
	if !found {
		t.Error("apple_chip_info metric missing from output")
	}
}
