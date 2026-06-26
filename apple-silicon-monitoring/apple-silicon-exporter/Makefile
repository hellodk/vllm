# apple-silicon-exporter Makefile
# Build and deploy a cross-platform Prometheus exporter for Apple Silicon metrics.

BINARY_NAME := apple-silicon-exporter
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo "1.0.0")
BUILD_TIME := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
LDFLAGS := -ldflags "-s -w -X main.version=$(VERSION) -X main.buildTime=$(BUILD_TIME)"

# Go settings
GO := go
GOFLAGS := -trimpath

# Host platform (used by the default `build` target)
HOST_OS := $(shell $(GO) env GOOS)
HOST_ARCH := $(shell $(GO) env GOARCH)
# CGO is required on macOS (IOKit/Metal); the Linux target is pure Go.
ifeq ($(HOST_OS),darwin)
HOST_CGO := 1
else
HOST_CGO := 0
endif

# Directories
BUILD_DIR := build
DIST_DIR := dist

# Installation paths (macOS)
PREFIX := /usr/local
BINDIR := $(PREFIX)/bin
LOGDIR := /var/log/apple-silicon-exporter
DATADIR := /var/lib/apple-silicon-exporter
LAUNCHD_DIR := /Library/LaunchDaemons
PLIST := com.company.apple-silicon-exporter.plist

.PHONY: all build build-darwin-arm64 build-linux-arm64 build-linux-amd64 \
	build-all release clean test test-coverage vet fmt fmt-check lint check \
	install uninstall start stop restart status run version help

all: build

# Build for the host platform.
build:
	@echo "Building $(BINARY_NAME) $(VERSION) for $(HOST_OS)/$(HOST_ARCH) (CGO=$(HOST_CGO))..."
	@mkdir -p $(BUILD_DIR)
	CGO_ENABLED=$(HOST_CGO) GOOS=$(HOST_OS) GOARCH=$(HOST_ARCH) \
		$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME) ./cmd/exporter

# macOS / Apple Silicon (requires the macOS SDK + Xcode command line tools).
build-darwin-arm64:
	@echo "Building darwin/arm64 (CGO enabled)..."
	@mkdir -p $(BUILD_DIR)
	CGO_ENABLED=1 GOOS=darwin GOARCH=arm64 \
		$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-darwin-arm64 ./cmd/exporter

# Linux on Apple Silicon (Asahi) and other ARM64 hosts. Pure Go, no CGO.
build-linux-arm64:
	@echo "Building linux/arm64 (CGO disabled)..."
	@mkdir -p $(BUILD_DIR)
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 \
		$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-linux-arm64 ./cmd/exporter

# Generic Linux x86-64. Pure Go, no CGO.
build-linux-amd64:
	@echo "Building linux/amd64 (CGO disabled)..."
	@mkdir -p $(BUILD_DIR)
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
		$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-linux-amd64 ./cmd/exporter

# Build every supported target. darwin/arm64 only succeeds on a macOS builder.
build-all: build-linux-amd64 build-linux-arm64 build-darwin-arm64

# Run tests for the host platform.
test:
	@echo "Running tests..."
	$(GO) test -race ./...

test-coverage:
	@echo "Running tests with coverage..."
	$(GO) test -race -coverprofile=coverage.out ./...
	$(GO) tool cover -html=coverage.out -o coverage.html

# Static analysis.
vet:
	@echo "Running go vet..."
	$(GO) vet ./...

# Lint (golangci-lint must be installed).
lint:
	@echo "Linting code..."
	@command -v golangci-lint >/dev/null 2>&1 || { echo "golangci-lint not installed"; exit 1; }
	golangci-lint run ./...

# Format code in place.
fmt:
	@echo "Formatting code..."
	gofmt -w .

# Fail if any file is not gofmt-clean (used in CI).
fmt-check:
	@echo "Checking gofmt..."
	@unformatted=$$(gofmt -l .); \
	if [ -n "$$unformatted" ]; then \
		echo "The following files are not gofmt-clean:"; \
		echo "$$unformatted"; \
		exit 1; \
	fi

# Aggregate verification target.
check: fmt-check vet test

clean:
	@echo "Cleaning..."
	rm -rf $(BUILD_DIR) $(DIST_DIR)
	rm -f coverage.out coverage.html

# Install (macOS, requires sudo).
install: build
	@echo "Installing $(BINARY_NAME)..."
	@if [ "$$(id -u)" -ne 0 ]; then echo "Please run with sudo"; exit 1; fi
	@mkdir -p $(BINDIR) $(LOGDIR) $(DATADIR)
	cp $(BUILD_DIR)/$(BINARY_NAME) $(BINDIR)/
	chmod 755 $(BINDIR)/$(BINARY_NAME)
	cp ../deploy/launchdaemons/$(PLIST) $(LAUNCHD_DIR)/
	chmod 644 $(LAUNCHD_DIR)/$(PLIST)
	chown root:wheel $(LAUNCHD_DIR)/$(PLIST)
	@echo "Installation complete. Run 'make start' to start the service."

uninstall: stop
	@echo "Uninstalling $(BINARY_NAME)..."
	@if [ "$$(id -u)" -ne 0 ]; then echo "Please run with sudo"; exit 1; fi
	rm -f $(BINDIR)/$(BINARY_NAME)
	rm -f $(LAUNCHD_DIR)/$(PLIST)
	@echo "Uninstallation complete. Log files preserved in $(LOGDIR)"

start:
	@echo "Starting $(BINARY_NAME)..."
	@if [ "$$(id -u)" -ne 0 ]; then echo "Please run with sudo"; exit 1; fi
	launchctl load $(LAUNCHD_DIR)/$(PLIST)
	@sleep 1
	@make status

stop:
	@echo "Stopping $(BINARY_NAME)..."
	@if [ "$$(id -u)" -ne 0 ]; then echo "Please run with sudo"; exit 1; fi
	-launchctl unload $(LAUNCHD_DIR)/$(PLIST) 2>/dev/null || true

restart: stop start

status:
	@echo "Service status:"
	@launchctl list | grep $(PLIST) || echo "Service not running"
	@echo ""
	@echo "Health check:"
	@curl -s http://127.0.0.1:9101/health || echo "Health endpoint not responding"
	@echo ""

# Run in development mode for the host platform.
run:
	@echo "Running in development mode..."
	$(GO) run ./cmd/exporter --listen=127.0.0.1:9101 --log-level=debug

# Create release packages for all targets.
release: clean build-all
	@echo "Creating release packages..."
	@mkdir -p $(DIST_DIR)
	@for f in $(BINARY_NAME)-linux-amd64 $(BINARY_NAME)-linux-arm64 $(BINARY_NAME)-darwin-arm64; do \
		if [ -f $(BUILD_DIR)/$$f ]; then cp $(BUILD_DIR)/$$f $(DIST_DIR)/; fi; \
	done
	@cd $(DIST_DIR) && for f in $(BINARY_NAME)-*; do \
		tar -czf $$f-$(VERSION).tar.gz $$f; \
	done
	@echo "Release artifacts written to $(DIST_DIR)/"

version:
	@echo $(VERSION)

help:
	@echo "apple-silicon-exporter Makefile"
	@echo ""
	@echo "Build targets:"
	@echo "  build               - Build for the host platform"
	@echo "  build-darwin-arm64  - Build macOS/Apple Silicon (CGO, macOS builder only)"
	@echo "  build-linux-arm64   - Build Linux arm64 (Asahi/ARM, pure Go)"
	@echo "  build-linux-amd64   - Build Linux x86-64 (pure Go)"
	@echo "  build-all           - Build every supported target"
	@echo "  release             - Build all targets and package tarballs"
	@echo ""
	@echo "Quality targets:"
	@echo "  test                - Run tests (race detector)"
	@echo "  test-coverage       - Run tests with HTML coverage report"
	@echo "  vet                 - Run go vet"
	@echo "  fmt                 - Format code with gofmt"
	@echo "  fmt-check           - Fail if code is not gofmt-clean"
	@echo "  lint                - Run golangci-lint"
	@echo "  check               - Run fmt-check + vet + test"
	@echo ""
	@echo "Service targets (macOS):"
	@echo "  install/uninstall/start/stop/restart/status"
	@echo ""
	@echo "  clean               - Remove build artifacts"
	@echo "  run                 - Run in development mode"
	@echo "  version             - Print the version string"
