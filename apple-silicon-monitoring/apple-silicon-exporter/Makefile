# apple-silicon-exporter Makefile
# Build and deploy Prometheus exporter for Apple Silicon metrics

BINARY_NAME := apple-silicon-exporter
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo "1.0.0")
BUILD_TIME := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
LDFLAGS := -ldflags "-s -w -X main.version=$(VERSION) -X main.buildTime=$(BUILD_TIME)"

# Go settings
GO := go
GOFLAGS := -trimpath
CGO_ENABLED := 1

# macOS specific
GOOS := darwin
GOARCH := arm64

# Directories
BUILD_DIR := build
DIST_DIR := dist

# Installation paths
PREFIX := /usr/local
BINDIR := $(PREFIX)/bin
LOGDIR := /var/log/apple-silicon-exporter
DATADIR := /var/lib/apple-silicon-exporter
LAUNCHD_DIR := /Library/LaunchDaemons
PLIST := com.company.apple-silicon-exporter.plist

.PHONY: all build clean test lint install uninstall start stop restart status

all: build

# Build the binary
build:
	@echo "Building $(BINARY_NAME) $(VERSION)..."
	@mkdir -p $(BUILD_DIR)
	CGO_ENABLED=$(CGO_ENABLED) GOOS=$(GOOS) GOARCH=$(GOARCH) \
		$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME) ./cmd/exporter

# Build for multiple architectures
build-all: build-arm64 build-amd64

build-arm64:
	@echo "Building for arm64..."
	@mkdir -p $(BUILD_DIR)
	CGO_ENABLED=$(CGO_ENABLED) GOOS=$(GOOS) GOARCH=arm64 \
		$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-darwin-arm64 ./cmd/exporter

build-amd64:
	@echo "Building for amd64..."
	@mkdir -p $(BUILD_DIR)
	CGO_ENABLED=$(CGO_ENABLED) GOOS=$(GOOS) GOARCH=amd64 \
		$(GO) build $(GOFLAGS) $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-darwin-amd64 ./cmd/exporter

# Create universal binary (requires both architectures built)
universal: build-arm64 build-amd64
	@echo "Creating universal binary..."
	@mkdir -p $(BUILD_DIR)
	lipo -create -output $(BUILD_DIR)/$(BINARY_NAME)-universal \
		$(BUILD_DIR)/$(BINARY_NAME)-darwin-arm64 \
		$(BUILD_DIR)/$(BINARY_NAME)-darwin-amd64

# Run tests
test:
	@echo "Running tests..."
	$(GO) test -v -race ./...

# Run tests with coverage
test-coverage:
	@echo "Running tests with coverage..."
	$(GO) test -v -race -coverprofile=coverage.out ./...
	$(GO) tool cover -html=coverage.out -o coverage.html

# Lint code
lint:
	@echo "Linting code..."
	@command -v golangci-lint >/dev/null 2>&1 || { echo "golangci-lint not installed"; exit 1; }
	golangci-lint run ./...

# Format code
fmt:
	@echo "Formatting code..."
	$(GO) fmt ./...
	goimports -w .

# Clean build artifacts
clean:
	@echo "Cleaning..."
	rm -rf $(BUILD_DIR) $(DIST_DIR)
	rm -f coverage.out coverage.html

# Install (requires sudo)
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

# Uninstall (requires sudo)
uninstall: stop
	@echo "Uninstalling $(BINARY_NAME)..."
	@if [ "$$(id -u)" -ne 0 ]; then echo "Please run with sudo"; exit 1; fi
	rm -f $(BINDIR)/$(BINARY_NAME)
	rm -f $(LAUNCHD_DIR)/$(PLIST)
	@echo "Uninstallation complete. Log files preserved in $(LOGDIR)"

# Service management
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

# Development helpers
run:
	@echo "Running in development mode..."
	$(GO) run ./cmd/exporter --listen=127.0.0.1:9101 --log-level=debug

# Create release package
release: clean build-all universal
	@echo "Creating release package..."
	@mkdir -p $(DIST_DIR)
	@cp $(BUILD_DIR)/$(BINARY_NAME)-darwin-arm64 $(DIST_DIR)/
	@cp $(BUILD_DIR)/$(BINARY_NAME)-darwin-amd64 $(DIST_DIR)/
	@cp $(BUILD_DIR)/$(BINARY_NAME)-universal $(DIST_DIR)/$(BINARY_NAME)
	@cp ../deploy/launchdaemons/$(PLIST) $(DIST_DIR)/
	@cp README.md $(DIST_DIR)/ 2>/dev/null || true
	@cd $(DIST_DIR) && tar -czf $(BINARY_NAME)-$(VERSION).tar.gz *
	@echo "Release package: $(DIST_DIR)/$(BINARY_NAME)-$(VERSION).tar.gz"

# Print version
version:
	@echo $(VERSION)

# Help
help:
	@echo "apple-silicon-exporter Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  build       - Build the binary"
	@echo "  build-all   - Build for all architectures"
	@echo "  universal   - Create universal binary"
	@echo "  test        - Run tests"
	@echo "  lint        - Lint code"
	@echo "  clean       - Clean build artifacts"
	@echo "  install     - Install binary and LaunchDaemon (sudo)"
	@echo "  uninstall   - Remove binary and LaunchDaemon (sudo)"
	@echo "  start       - Start the service (sudo)"
	@echo "  stop        - Stop the service (sudo)"
	@echo "  restart     - Restart the service (sudo)"
	@echo "  status      - Check service status"
	@echo "  run         - Run in development mode"
	@echo "  release     - Create release package"
	@echo "  help        - Show this help"
