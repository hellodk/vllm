// Package chip provides Apple Silicon chip-family and variant detection.
//
// Detection is intentionally generic: it scans for an "M<n>" token followed by
// an optional "Pro"/"Max"/"Ultra" variant. This recognises M1, M2, M3, M4, M5
// and any future M-series part without requiring an exhaustive table. When no
// M-series token can be found the raw model identifier is reported instead so
// that the exporter never crashes or hides hardware it does not recognise.
package chip

import (
	"regexp"
	"strings"
)

// Info describes the detected host chip.
type Info struct {
	// Family is the M-series generation, e.g. "M1", "M4". Empty when unknown.
	Family string
	// Variant is the SKU tier, e.g. "Pro", "Max", "Ultra". Empty for the base part.
	Variant string
	// Name is the human-friendly chip name, e.g. "M4 Max". Falls back to the raw
	// model string when the family cannot be determined.
	Name string
	// Model is the raw machine/model identifier (hw.model, device-tree model, or
	// SoC code), preserved for debugging and as a fallback label.
	Model string
	// Source records where the information came from (sysctl, device-tree, cpuinfo).
	Source string
}

// brandRe matches an Apple M-series token such as "M1", "M2 Pro", "M4 Max" or
// "M5 Ultra". The variant group is optional so base parts are matched too.
var brandRe = regexp.MustCompile(`(?i)\bM([0-9]+)\s*(Pro|Max|Ultra)?\b`)

// parseBrand extracts the family ("M<n>") and variant ("Pro"/"Max"/"Ultra")
// from an arbitrary brand or model string. ok is false when no token is found.
func parseBrand(s string) (family, variant string, ok bool) {
	m := brandRe.FindStringSubmatch(s)
	if m == nil {
		return "", "", false
	}
	family = "M" + m[1]
	if m[2] != "" {
		variant = canonicalVariant(m[2])
	}
	return family, variant, true
}

func canonicalVariant(v string) string {
	switch strings.ToLower(v) {
	case "pro":
		return "Pro"
	case "max":
		return "Max"
	case "ultra":
		return "Ultra"
	default:
		return ""
	}
}

// infoFromBrand builds an Info from a brand string, falling back to model/raw
// when the brand does not contain a recognisable M-series token.
func infoFromBrand(brand, model, source string) Info {
	info := Info{Model: model, Source: source}
	if fam, variant, ok := parseBrand(brand); ok {
		info.Family = fam
		info.Variant = variant
		info.Name = fam
		if variant != "" {
			info.Name = fam + " " + variant
		}
		return info
	}
	info.Name = firstNonEmpty(strings.TrimSpace(model), strings.TrimSpace(brand), "unknown")
	return info
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
