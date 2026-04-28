// apple-silicon-exporter - Prometheus exporter for Apple Silicon metrics
// Collects GPU, thermal, power, and system metrics from M-series Macs

package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/spf13/pflag"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"

	"github.com/company/apple-silicon-exporter/internal/collector"
)

var (
	version   = "1.0.0"
	buildTime = "unknown"
)

type Config struct {
	ListenAddress       string
	MetricsPath         string
	PowermetricsPath    string
	PowermetricsSamples int
	PowermetricsInterval time.Duration
	LogLevel            string
	EnableIOKit         bool
	EnablePowermetrics  bool
	EnableMetal         bool
}

func main() {
	cfg := &Config{}

	pflag.StringVar(&cfg.ListenAddress, "listen", "127.0.0.1:9101", "Address to listen on for metrics")
	pflag.StringVar(&cfg.MetricsPath, "metrics-path", "/metrics", "Path under which to expose metrics")
	pflag.StringVar(&cfg.PowermetricsPath, "powermetrics-path", "/usr/bin/powermetrics", "Path to powermetrics binary")
	pflag.IntVar(&cfg.PowermetricsSamples, "powermetrics-samples", 1, "Number of powermetrics samples per scrape")
	pflag.DurationVar(&cfg.PowermetricsInterval, "powermetrics-interval", 1000*time.Millisecond, "Interval between powermetrics samples")
	pflag.StringVar(&cfg.LogLevel, "log-level", "info", "Log level (debug, info, warn, error)")
	pflag.BoolVar(&cfg.EnableIOKit, "enable-iokit", true, "Enable IOKit-based metrics collection")
	pflag.BoolVar(&cfg.EnablePowermetrics, "enable-powermetrics", true, "Enable powermetrics-based collection")
	pflag.BoolVar(&cfg.EnableMetal, "enable-metal", true, "Enable Metal performance counters")

	showVersion := pflag.Bool("version", false, "Show version and exit")
	pflag.Parse()

	if *showVersion {
		fmt.Printf("apple-silicon-exporter version %s (built %s)\n", version, buildTime)
		os.Exit(0)
	}

	// Initialize logger
	logger := initLogger(cfg.LogLevel)
	defer logger.Sync()

	logger.Info("Starting apple-silicon-exporter",
		zap.String("version", version),
		zap.String("listen", cfg.ListenAddress),
	)

	// Create and register collectors
	registry := prometheus.NewRegistry()

	// Add standard Go metrics
	registry.MustRegister(prometheus.NewGoCollector())
	registry.MustRegister(prometheus.NewProcessCollector(prometheus.ProcessCollectorOpts{}))

	// Create the Apple Silicon collector
	appleCollector, err := collector.NewAppleSiliconCollector(
		logger,
		collector.WithIOKit(cfg.EnableIOKit),
		collector.WithPowermetrics(cfg.EnablePowermetrics, cfg.PowermetricsPath, cfg.PowermetricsSamples, cfg.PowermetricsInterval),
		collector.WithMetal(cfg.EnableMetal),
	)
	if err != nil {
		logger.Fatal("Failed to create collector", zap.Error(err))
	}

	registry.MustRegister(appleCollector)

	// Setup HTTP server
	mux := http.NewServeMux()

	// Metrics endpoint
	mux.Handle(cfg.MetricsPath, promhttp.HandlerFor(
		registry,
		promhttp.HandlerOpts{
			EnableOpenMetrics:   true,
			MaxRequestsInFlight: 10,
			Timeout:             30 * time.Second,
		},
	))

	// Health endpoint
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	// Ready endpoint
	mux.HandleFunc("/ready", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("Ready"))
	})

	// Root endpoint with info
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte(fmt.Sprintf(`<!DOCTYPE html>
<html>
<head><title>Apple Silicon Exporter</title></head>
<body>
<h1>Apple Silicon Exporter</h1>
<p>Version: %s</p>
<p><a href="%s">Metrics</a></p>
<p><a href="/health">Health</a></p>
</body>
</html>`, version, cfg.MetricsPath)))
	})

	server := &http.Server{
		Addr:         cfg.ListenAddress,
		Handler:      mux,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 60 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	// Graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigCh
		logger.Info("Shutting down server...")
		shutdownCtx, shutdownCancel := context.WithTimeout(ctx, 10*time.Second)
		defer shutdownCancel()
		server.Shutdown(shutdownCtx)
	}()

	logger.Info("Server listening", zap.String("address", cfg.ListenAddress))
	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		logger.Fatal("Server error", zap.Error(err))
	}

	logger.Info("Server stopped")
}

func initLogger(level string) *zap.Logger {
	var zapLevel zapcore.Level
	switch level {
	case "debug":
		zapLevel = zapcore.DebugLevel
	case "info":
		zapLevel = zapcore.InfoLevel
	case "warn":
		zapLevel = zapcore.WarnLevel
	case "error":
		zapLevel = zapcore.ErrorLevel
	default:
		zapLevel = zapcore.InfoLevel
	}

	config := zap.Config{
		Level:            zap.NewAtomicLevelAt(zapLevel),
		Development:      false,
		Encoding:         "json",
		EncoderConfig:    zap.NewProductionEncoderConfig(),
		OutputPaths:      []string{"stdout"},
		ErrorOutputPaths: []string{"stderr"},
	}

	logger, err := config.Build()
	if err != nil {
		panic(err)
	}
	return logger
}
