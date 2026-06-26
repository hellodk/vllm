//go:build linux

package powermetrics

import (
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"
)

// errNoPower indicates no system-power source was available on this host.
var errNoPower = errors.New("powermetrics: no power source available")

// Collector derives system power from Linux sysfs.
type Collector struct {
	logger *zap.Logger
	root   string // filesystem root, overridable for tests; defaults to "/"

	mu       sync.Mutex
	prevRAPL uint64
	prevTime time.Time
	haveRAPL bool
	nowFunc  func() time.Time
}

// NewCollector creates a new sysfs-backed power collector. The path, samples
// and interval arguments are accepted for API parity with the macOS collector
// but are unused on Linux.
func NewCollector(logger *zap.Logger, path string, samples int, interval time.Duration) *Collector {
	return &Collector{
		logger:  logger,
		root:    "/",
		nowFunc: time.Now,
	}
}

// Collect reads system power from hwmon (instantaneous) or RAPL (energy delta).
func (c *Collector) Collect() (*Metrics, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	metrics := &Metrics{}

	// Prefer instantaneous hwmon power sensors (common on Asahi/ARM).
	if watts, ok := readHwmonPower(filepath.Join(c.root, "sys", "class", "hwmon")); ok {
		metrics.SystemPower = watts
		metrics.HasSystemPower = true
		return metrics, nil
	}

	// Fall back to RAPL energy counters (Intel/AMD), which require a delta.
	if uj, ok := readRAPLEnergy(filepath.Join(c.root, "sys", "class", "powercap")); ok {
		now := c.nowFunc()
		if c.haveRAPL {
			if watts, ok := computePowerFromEnergy(c.prevRAPL, uj, now.Sub(c.prevTime)); ok {
				metrics.SystemPower = watts
				metrics.HasSystemPower = true
			}
		}
		c.prevRAPL = uj
		c.prevTime = now
		c.haveRAPL = true
		if metrics.HasSystemPower {
			return metrics, nil
		}
		// First sample only primes the delta; report not-yet-available.
		return nil, errNoPower
	}

	return nil, errNoPower
}

// readHwmonPower sums all power*_input sensors (microwatts) under a hwmon
// directory and returns the total in watts.
func readHwmonPower(hwmonDir string) (float64, bool) {
	entries, err := os.ReadDir(hwmonDir)
	if err != nil {
		return 0, false
	}

	var totalMicroWatts float64
	found := false
	for _, e := range entries {
		dir := filepath.Join(hwmonDir, e.Name())
		files, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, f := range files {
			name := f.Name()
			if !strings.HasPrefix(name, "power") || !strings.HasSuffix(name, "_input") {
				continue
			}
			raw := readTrimmed(filepath.Join(dir, name))
			if v, err := strconv.ParseFloat(raw, 64); err == nil {
				totalMicroWatts += v
				found = true
			}
		}
	}
	if !found {
		return 0, false
	}
	return totalMicroWatts / 1e6, true
}

// readRAPLEnergy reads the package energy counter (microjoules) from the first
// RAPL powercap zone.
func readRAPLEnergy(powercapDir string) (uint64, bool) {
	entries, err := os.ReadDir(powercapDir)
	if err != nil {
		return 0, false
	}

	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "intel-rapl:") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)

	for _, name := range names {
		raw := readTrimmed(filepath.Join(powercapDir, name, "energy_uj"))
		if raw == "" {
			continue
		}
		if v, err := strconv.ParseUint(raw, 10, 64); err == nil {
			return v, true
		}
	}
	return 0, false
}

// computePowerFromEnergy converts an energy delta (microjoules) over a time
// interval into average power (watts). It returns ok=false for non-positive
// intervals or counter wrap-around.
func computePowerFromEnergy(prevUJ, curUJ uint64, dt time.Duration) (float64, bool) {
	if dt <= 0 || curUJ < prevUJ {
		return 0, false
	}
	deltaJoules := float64(curUJ-prevUJ) / 1e6
	return deltaJoules / dt.Seconds(), true
}

func readTrimmed(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}
