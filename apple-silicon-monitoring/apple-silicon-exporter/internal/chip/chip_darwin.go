//go:build darwin

package chip

import (
	"os/exec"
	"strings"
)

// Detect identifies the host chip on macOS via sysctl. It prefers the CPU
// brand string (e.g. "Apple M4 Max") and falls back to hw.model.
func Detect() Info {
	brand := sysctlString("machdep.cpu.brand_string")
	model := sysctlString("hw.model")
	return infoFromBrand(brand, model, "sysctl")
}

func sysctlString(name string) string {
	out, err := exec.Command("sysctl", "-n", name).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
