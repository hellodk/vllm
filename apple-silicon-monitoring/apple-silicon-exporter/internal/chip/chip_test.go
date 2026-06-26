package chip

import "testing"

func TestParseBrand(t *testing.T) {
	tests := []struct {
		name        string
		in          string
		wantFamily  string
		wantVariant string
		wantOK      bool
	}{
		{"m1 base", "Apple M1", "M1", "", true},
		{"m1 pro", "Apple M1 Pro", "M1", "Pro", true},
		{"m1 max", "Apple M1 Max", "M1", "Max", true},
		{"m1 ultra", "Apple M1 Ultra", "M1", "Ultra", true},
		{"m2", "Apple M2", "M2", "", true},
		{"m3 max", "Apple M3 Max", "M3", "Max", true},
		{"m4 max", "Apple M4 Max", "M4", "Max", true},
		{"m5 ultra future", "Apple M5 Ultra", "M5", "Ultra", true},
		{"m6 unknown future", "Apple M6 Pro", "M6", "Pro", true},
		{"lowercase variant", "apple m4 max", "M4", "Max", true},
		{"dt model string", "Apple MacBook Pro (14-inch, M3 Pro, Nov 2023)", "M3", "Pro", true},
		{"no token", "Intel Core i7", "", "", false},
		{"empty", "", "", "", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			fam, variant, ok := parseBrand(tt.in)
			if ok != tt.wantOK || fam != tt.wantFamily || variant != tt.wantVariant {
				t.Fatalf("parseBrand(%q) = (%q,%q,%v), want (%q,%q,%v)",
					tt.in, fam, variant, ok, tt.wantFamily, tt.wantVariant, tt.wantOK)
			}
		})
	}
}

func TestInfoFromBrand(t *testing.T) {
	tests := []struct {
		name     string
		brand    string
		model    string
		wantName string
		wantFam  string
	}{
		{"recognised", "Apple M4 Max", "Mac15,3", "M4 Max", "M4"},
		{"base part", "Apple M2", "Mac14,2", "M2", "M2"},
		{"unknown falls back to model", "Some Future CPU", "MacXX,9", "MacXX,9", ""},
		{"unknown no model falls back to brand", "Mystery Silicon", "", "Mystery Silicon", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := infoFromBrand(tt.brand, tt.model, "test")
			if got.Name != tt.wantName || got.Family != tt.wantFam {
				t.Fatalf("infoFromBrand(%q,%q) = name=%q family=%q, want name=%q family=%q",
					tt.brand, tt.model, got.Name, got.Family, tt.wantName, tt.wantFam)
			}
		})
	}
}
