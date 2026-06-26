//go:build linux

package chip

import (
	"os"
	"regexp"
	"strings"
)

// socCodeRe matches an Apple SoC code such as "apple,t6022" found in the
// device-tree "compatible" property on Asahi Linux.
var socCodeRe = regexp.MustCompile(`apple,(t[0-9a-fA-F]+)`)

// socCodeToName is a best-effort map from Apple SoC codes to marketing names.
// It is intentionally non-exhaustive: unknown codes fall back to the raw code
// so that future silicon is still reported rather than mislabelled.
var socCodeToName = map[string]string{
	"t8103": "M1",
	"t6000": "M1 Pro",
	"t6001": "M1 Max",
	"t6002": "M1 Ultra",
	"t8112": "M2",
	"t6020": "M2 Pro",
	"t6021": "M2 Max",
	"t6022": "M2 Ultra",
	"t8122": "M3",
	"t6030": "M3 Pro",
	"t6031": "M3 Max",
	"t6034": "M3 Max",
	"t8132": "M4",
	"t6040": "M4 Pro",
	"t6041": "M4 Max",
}

// Detect identifies the host chip on Linux (including Asahi Linux on Apple
// Silicon). It reads the flattened device-tree and /proc/cpuinfo.
func Detect() Info {
	model := readCStr("/proc/device-tree/model")
	cpuinfo, _ := os.ReadFile("/proc/cpuinfo")
	compatible := readCStr("/proc/device-tree/compatible")
	return detectLinux(model, string(cpuinfo), compatible)
}

// detectLinux is the pure detection logic, separated for testing. Inputs are
// the device-tree model string, the contents of /proc/cpuinfo, and the
// (NUL-joined or space-joined) device-tree compatible string.
func detectLinux(model, cpuinfo, compatible string) Info {
	// 1. Device-tree model string usually contains a marketing name such as
	//    "Apple MacBook Pro (14-inch, M3 Pro, Nov 2023)".
	if fam, variant, ok := parseBrand(model); ok {
		return buildInfo(fam, variant, model, "device-tree")
	}

	// 2. Some firmwares expose the family via /proc/cpuinfo (Hardware/Model lines).
	if fam, variant, ok := parseBrand(cpuinfo); ok {
		return buildInfo(fam, variant, firstNonEmpty(model, "cpuinfo"), "cpuinfo")
	}

	// 3. Fall back to the Apple SoC code in the device-tree compatible property.
	if code := extractSoCCode(compatible); code != "" {
		if name, known := socCodeToName[code]; known {
			fam, variant, _ := parseBrand(name)
			return buildInfo(fam, variant, firstNonEmpty(model, code), "device-tree")
		}
		// Unknown SoC code: report it raw rather than guessing.
		return Info{Model: firstNonEmpty(model, code), Name: code, Source: "device-tree"}
	}

	// 4. Nothing recognisable: report whatever raw identifier we have.
	return infoFromBrand("", firstNonEmpty(strings.TrimSpace(model), "unknown"), "device-tree")
}

func buildInfo(family, variant, model, source string) Info {
	name := family
	if variant != "" {
		name = family + " " + variant
	}
	return Info{Family: family, Variant: variant, Name: name, Model: model, Source: source}
}

func extractSoCCode(compatible string) string {
	m := socCodeRe.FindStringSubmatch(compatible)
	if m == nil {
		return ""
	}
	return strings.ToLower(m[1])
}

// readCStr reads a device-tree property file and converts NUL separators to
// spaces, trimming the trailing NUL that device-tree strings carry.
func readCStr(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	s := strings.ReplaceAll(string(data), "\x00", " ")
	return strings.TrimSpace(s)
}
