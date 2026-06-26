//go:build linux

package chip

import "testing"

func TestDetectLinux(t *testing.T) {
	tests := []struct {
		name       string
		model      string
		cpuinfo    string
		compatible string
		wantName   string
		wantFamily string
	}{
		{
			name:       "device-tree model wins",
			model:      "Apple MacBook Pro (14-inch, M3 Pro, Nov 2023)",
			compatible: "apple,j414s apple,t6030 apple,arm-platform",
			wantName:   "M3 Pro",
			wantFamily: "M3",
		},
		{
			name:       "soc code fallback known",
			model:      "",
			compatible: "apple,j314s apple,t6001 apple,arm-platform",
			wantName:   "M1 Max",
			wantFamily: "M1",
		},
		{
			name:       "soc code fallback unknown reports raw",
			model:      "",
			compatible: "apple,jXXX apple,t9999 apple,arm-platform",
			wantName:   "t9999",
			wantFamily: "",
		},
		{
			name:       "cpuinfo fallback",
			model:      "",
			cpuinfo:    "processor : 0\nHardware : Apple M2 Max\n",
			compatible: "",
			wantName:   "M2 Max",
			wantFamily: "M2",
		},
		{
			name:       "nothing recognisable",
			model:      "Generic ARM Board",
			compatible: "vendor,board",
			wantName:   "Generic ARM Board",
			wantFamily: "",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := detectLinux(tt.model, tt.cpuinfo, tt.compatible)
			if got.Name != tt.wantName || got.Family != tt.wantFamily {
				t.Fatalf("detectLinux() = name=%q family=%q, want name=%q family=%q",
					got.Name, got.Family, tt.wantName, tt.wantFamily)
			}
		})
	}
}
