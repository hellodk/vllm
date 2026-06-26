//go:build linux

package metal

import (
	"errors"

	"go.uber.org/zap"
)

// errUnsupported signals that Metal is not available on this platform.
var errUnsupported = errors.New("metal: not supported on linux")

// Collector is a no-op collector on Linux.
type Collector struct{}

// NewCollector returns an error on Linux so the parent collector disables
// Metal-based collection and degrades gracefully.
func NewCollector(_ *zap.Logger) (*Collector, error) {
	return nil, errUnsupported
}

// Collect always reports that Metal is unsupported on Linux.
func (c *Collector) Collect() (*Metrics, error) {
	return nil, errUnsupported
}
