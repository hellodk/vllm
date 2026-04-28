// Package llmtelemetry provides OpenTelemetry instrumentation for LLM inference workloads.
//
// Features:
//   - Inference latency and throughput metrics
//   - Token processing statistics
//   - GPU memory tracking
//   - Hallucination detection signals (entropy, repetition, confidence)
//   - Error classification and model health scoring
//
// Usage:
//
//	telemetry, _ := llmtelemetry.New("llama-3-70b",
//	    llmtelemetry.WithEndpoint("localhost:4317"),
//	)
//	defer telemetry.Shutdown()
//
//	telemetry.RecordInference(llmtelemetry.InferenceResult{
//	    PromptTokens:     100,
//	    OutputTokens:     50,
//	    PromptLatency:    500 * time.Millisecond,
//	    InferenceLatency: 2 * time.Second,
//	})
package llmtelemetry

import (
	"context"
	"math"
	"strings"
	"sync"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/metric"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
)

// ErrorType classifies inference errors
type ErrorType string

const (
	ErrorInvalidInput    ErrorType = "invalid_input"
	ErrorContextTooLong  ErrorType = "context_too_long"
	ErrorOutOfMemory     ErrorType = "out_of_memory"
	ErrorTimeout         ErrorType = "timeout"
	ErrorKernelFailure   ErrorType = "kernel_failure"
	ErrorMetalCrash      ErrorType = "metal_crash"
	ErrorModelNotLoaded  ErrorType = "model_not_loaded"
	ErrorUnknown         ErrorType = "unknown"
)

// InferenceResult contains the results of an inference operation
type InferenceResult struct {
	PromptTokens     int
	OutputTokens     int
	PromptLatency    time.Duration
	InferenceLatency time.Duration
	TokenProbs       []float64 // Optional: token probabilities for hallucination detection
	OutputText       string    // Optional: generated text for analysis
	Error            ErrorType // Empty if no error
	ContextLength    int       // Optional: total context length
	BatchSize        int       // Default: 1
}

// Options configures the telemetry instance
type Options struct {
	Endpoint       string
	ServiceName    string
	ServiceVersion string
	Insecure       bool
	ExportInterval time.Duration
	ModelVersion   string
	GPUID          string
}

// Option is a functional option for configuring telemetry
type Option func(*Options)

// WithEndpoint sets the OTLP endpoint
func WithEndpoint(endpoint string) Option {
	return func(o *Options) {
		o.Endpoint = endpoint
	}
}

// WithServiceName sets the service name
func WithServiceName(name string) Option {
	return func(o *Options) {
		o.ServiceName = name
	}
}

// WithInsecure enables insecure connection
func WithInsecure(insecure bool) Option {
	return func(o *Options) {
		o.Insecure = insecure
	}
}

// WithModelVersion sets the model version
func WithModelVersion(version string) Option {
	return func(o *Options) {
		o.ModelVersion = version
	}
}

// WithGPUID sets the GPU identifier
func WithGPUID(id string) Option {
	return func(o *Options) {
		o.GPUID = id
	}
}

// LLMTelemetry provides metrics collection for LLM inference
type LLMTelemetry struct {
	modelName    string
	modelVersion string
	gpuID        string
	
	meter        metric.Meter
	provider     *sdkmetric.MeterProvider
	
	// Metrics
	inferenceRequests    metric.Int64Counter
	inferenceDuration    metric.Float64Histogram
	tokensProcessed      metric.Int64Counter
	tokensPerSecond      metric.Float64Gauge
	batchSize            metric.Int64Histogram
	contextLength        metric.Int64Histogram
	gpuMemoryAllocated   metric.Int64Gauge
	kvCacheUtilization   metric.Float64Gauge
	queueDepth           metric.Int64Gauge
	errorsTotal          metric.Int64Counter
	modelLoaded          metric.Int64Gauge
	outputEntropy        metric.Float64Histogram
	confidenceMean       metric.Float64Gauge
	repetitionScore      metric.Float64Gauge
	perplexity           metric.Float64Histogram
	modelHealthScore     metric.Float64Gauge
	
	// State tracking
	mu              sync.Mutex
	requestCount    int64
	errorCount      int64
	entropyHistory  []float64
	attrs           []attribute.KeyValue
}

// New creates a new LLMTelemetry instance
func New(modelName string, opts ...Option) (*LLMTelemetry, error) {
	options := &Options{
		Endpoint:       "localhost:4317",
		ServiceName:    "llm-inference",
		ServiceVersion: "1.0.0",
		Insecure:       true,
		ExportInterval: 10 * time.Second,
		ModelVersion:   "unknown",
		GPUID:          "0",
	}
	
	for _, opt := range opts {
		opt(options)
	}
	
	ctx := context.Background()
	
	// Create OTLP exporter
	exporterOpts := []otlpmetricgrpc.Option{
		otlpmetricgrpc.WithEndpoint(options.Endpoint),
	}
	if options.Insecure {
		exporterOpts = append(exporterOpts, otlpmetricgrpc.WithInsecure())
	}
	
	exporter, err := otlpmetricgrpc.New(ctx, exporterOpts...)
	if err != nil {
		return nil, err
	}
	
	// Create resource
	res, err := resource.New(ctx,
		resource.WithAttributes(
			semconv.ServiceName(options.ServiceName),
			semconv.ServiceVersion(options.ServiceVersion),
			attribute.String("platform", "apple-silicon"),
		),
	)
	if err != nil {
		return nil, err
	}
	
	// Create meter provider
	provider := sdkmetric.NewMeterProvider(
		sdkmetric.WithResource(res),
		sdkmetric.WithReader(
			sdkmetric.NewPeriodicReader(exporter,
				sdkmetric.WithInterval(options.ExportInterval),
			),
		),
	)
	
	otel.SetMeterProvider(provider)
	
	t := &LLMTelemetry{
		modelName:    modelName,
		modelVersion: options.ModelVersion,
		gpuID:        options.GPUID,
		provider:     provider,
		meter:        provider.Meter("llm-inference"),
		attrs: []attribute.KeyValue{
			attribute.String("model", modelName),
			attribute.String("model_version", options.ModelVersion),
			attribute.String("gpu", options.GPUID),
		},
		entropyHistory: make([]float64, 0, 100),
	}
	
	if err := t.initMetrics(); err != nil {
		return nil, err
	}
	
	return t, nil
}

func (t *LLMTelemetry) initMetrics() error {
	var err error
	
	t.inferenceRequests, err = t.meter.Int64Counter("llm_inference_requests_total",
		metric.WithDescription("Total number of inference requests"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.inferenceDuration, err = t.meter.Float64Histogram("llm_inference_duration_seconds",
		metric.WithDescription("Inference latency by phase"),
		metric.WithUnit("s"),
	)
	if err != nil {
		return err
	}
	
	t.tokensProcessed, err = t.meter.Int64Counter("llm_tokens_processed_total",
		metric.WithDescription("Total tokens processed"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.tokensPerSecond, err = t.meter.Float64Gauge("llm_tokens_per_second",
		metric.WithDescription("Current token generation throughput"),
		metric.WithUnit("1/s"),
	)
	if err != nil {
		return err
	}
	
	t.batchSize, err = t.meter.Int64Histogram("llm_batch_size",
		metric.WithDescription("Batch sizes processed"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.contextLength, err = t.meter.Int64Histogram("llm_context_length",
		metric.WithDescription("Context window utilization"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.gpuMemoryAllocated, err = t.meter.Int64Gauge("llm_gpu_memory_allocated_bytes",
		metric.WithDescription("GPU memory allocated for model"),
		metric.WithUnit("By"),
	)
	if err != nil {
		return err
	}
	
	t.kvCacheUtilization, err = t.meter.Float64Gauge("llm_kv_cache_utilization",
		metric.WithDescription("KV cache utilization"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.queueDepth, err = t.meter.Int64Gauge("llm_queue_depth",
		metric.WithDescription("Number of pending requests"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.errorsTotal, err = t.meter.Int64Counter("llm_error_total",
		metric.WithDescription("Total errors by type"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.modelLoaded, err = t.meter.Int64Gauge("llm_model_loaded",
		metric.WithDescription("Whether the model is loaded"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.outputEntropy, err = t.meter.Float64Histogram("llm_output_entropy",
		metric.WithDescription("Token probability entropy"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.confidenceMean, err = t.meter.Float64Gauge("llm_confidence_mean",
		metric.WithDescription("Mean token confidence score"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.repetitionScore, err = t.meter.Float64Gauge("llm_repetition_score",
		metric.WithDescription("N-gram repetition score"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.perplexity, err = t.meter.Float64Histogram("llm_perplexity",
		metric.WithDescription("Output perplexity"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	t.modelHealthScore, err = t.meter.Float64Gauge("llm_model_health_score",
		metric.WithDescription("Composite model health score"),
		metric.WithUnit("1"),
	)
	if err != nil {
		return err
	}
	
	return nil
}

// RecordInference records metrics for a completed inference
func (t *LLMTelemetry) RecordInference(ctx context.Context, result InferenceResult) {
	t.mu.Lock()
	defer t.mu.Unlock()
	
	batchSize := result.BatchSize
	if batchSize < 1 {
		batchSize = 1
	}
	
	// Request count
	status := "success"
	if result.Error != "" {
		status = "error"
	}
	
	attrs := append(t.attrs, attribute.String("status", status))
	t.inferenceRequests.Add(ctx, 1, metric.WithAttributes(attrs...))
	t.requestCount++
	
	// Handle errors
	if result.Error != "" {
		errorAttrs := append(t.attrs, attribute.String("error_type", string(result.Error)))
		t.errorsTotal.Add(ctx, 1, metric.WithAttributes(errorAttrs...))
		t.errorCount++
		return
	}
	
	// Latency
	promptAttrs := append(t.attrs, attribute.String("phase", "prompt"))
	genAttrs := append(t.attrs, attribute.String("phase", "generation"))
	totalAttrs := append(t.attrs, attribute.String("phase", "total"))
	
	t.inferenceDuration.Record(ctx, result.PromptLatency.Seconds(), metric.WithAttributes(promptAttrs...))
	t.inferenceDuration.Record(ctx, result.InferenceLatency.Seconds(), metric.WithAttributes(genAttrs...))
	t.inferenceDuration.Record(ctx, (result.PromptLatency + result.InferenceLatency).Seconds(), metric.WithAttributes(totalAttrs...))
	
	// Tokens
	inputAttrs := append(t.attrs, attribute.String("direction", "input"))
	outputAttrs := append(t.attrs, attribute.String("direction", "output"))
	
	t.tokensProcessed.Add(ctx, int64(result.PromptTokens), metric.WithAttributes(inputAttrs...))
	t.tokensProcessed.Add(ctx, int64(result.OutputTokens), metric.WithAttributes(outputAttrs...))
	
	// Throughput
	if result.InferenceLatency > 0 {
		tps := float64(result.OutputTokens) / result.InferenceLatency.Seconds()
		t.tokensPerSecond.Record(ctx, tps, metric.WithAttributes(t.attrs...))
	}
	
	// Batch and context
	t.batchSize.Record(ctx, int64(batchSize), metric.WithAttributes(t.attrs...))
	if result.ContextLength > 0 {
		t.contextLength.Record(ctx, int64(result.ContextLength), metric.WithAttributes(t.attrs...))
	}
	
	// Hallucination detection
	if len(result.TokenProbs) > 0 {
		t.recordHallucinationMetrics(ctx, result.TokenProbs, result.OutputText)
	}
}

func (t *LLMTelemetry) recordHallucinationMetrics(ctx context.Context, probs []float64, text string) {
	// Entropy
	entropy := computeEntropy(probs)
	t.outputEntropy.Record(ctx, entropy, metric.WithAttributes(t.attrs...))
	
	t.entropyHistory = append(t.entropyHistory, entropy)
	if len(t.entropyHistory) > 100 {
		t.entropyHistory = t.entropyHistory[1:]
	}
	
	// Confidence
	mean := computeMean(probs)
	t.confidenceMean.Record(ctx, mean, metric.WithAttributes(t.attrs...))
	
	// Perplexity
	ppl := computePerplexity(probs)
	t.perplexity.Record(ctx, ppl, metric.WithAttributes(t.attrs...))
	
	// Repetition (if text provided)
	if text != "" {
		rep := computeRepetition(text)
		t.repetitionScore.Record(ctx, rep, metric.WithAttributes(t.attrs...))
	}
}

// RecordGPUMemory records current GPU memory allocation
func (t *LLMTelemetry) RecordGPUMemory(ctx context.Context, bytes int64) {
	t.gpuMemoryAllocated.Record(ctx, bytes, metric.WithAttributes(t.attrs...))
}

// RecordKVCache records KV cache utilization
func (t *LLMTelemetry) RecordKVCache(ctx context.Context, utilization float64) {
	t.kvCacheUtilization.Record(ctx, utilization, metric.WithAttributes(t.attrs...))
}

// RecordQueueDepth records current queue depth
func (t *LLMTelemetry) RecordQueueDepth(ctx context.Context, depth int64) {
	t.queueDepth.Record(ctx, depth, metric.WithAttributes(t.attrs...))
}

// SetModelLoaded sets the model loaded status
func (t *LLMTelemetry) SetModelLoaded(ctx context.Context, loaded bool) {
	val := int64(0)
	if loaded {
		val = 1
	}
	t.modelLoaded.Record(ctx, val, metric.WithAttributes(t.attrs...))
}

// ComputeHealthScore computes and records the model health score
func (t *LLMTelemetry) ComputeHealthScore(ctx context.Context) float64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	
	score := 100.0
	
	// Error rate penalty
	if t.requestCount > 0 {
		errorRate := float64(t.errorCount) / float64(t.requestCount)
		score -= math.Min(50, errorRate*100)
	}
	
	// Entropy penalty
	if len(t.entropyHistory) > 0 {
		avgEntropy := computeMean(t.entropyHistory)
		entropyPenalty := math.Min(25, (avgEntropy/4)*25)
		score -= entropyPenalty
	}
	
	score = math.Max(0, math.Min(100, score))
	t.modelHealthScore.Record(ctx, score, metric.WithAttributes(t.attrs...))
	
	return score
}

// Shutdown shuts down the telemetry provider
func (t *LLMTelemetry) Shutdown(ctx context.Context) error {
	return t.provider.Shutdown(ctx)
}

// Helper functions

func computeEntropy(probs []float64) float64 {
	if len(probs) == 0 {
		return 0
	}
	
	entropy := 0.0
	for _, p := range probs {
		if p > 1e-10 {
			entropy -= p * math.Log(p+1e-10)
		}
	}
	return entropy
}

func computePerplexity(probs []float64) float64 {
	if len(probs) == 0 {
		return 1
	}
	
	logProbSum := 0.0
	for _, p := range probs {
		logProbSum += math.Log(p + 1e-10)
	}
	
	return math.Exp(-logProbSum / float64(len(probs)))
}

func computeMean(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	
	sum := 0.0
	for _, v := range vals {
		sum += v
	}
	return sum / float64(len(vals))
}

func computeRepetition(text string) float64 {
	words := strings.Fields(strings.ToLower(text))
	n := 3
	
	if len(words) < n {
		return 0
	}
	
	ngrams := make(map[string]int)
	for i := 0; i <= len(words)-n; i++ {
		key := strings.Join(words[i:i+n], " ")
		ngrams[key]++
	}
	
	repeated := 0
	for _, count := range ngrams {
		if count > 1 {
			repeated += count - 1
		}
	}
	
	total := len(words) - n + 1
	if total <= 0 {
		return 0
	}
	
	return float64(repeated) / float64(total)
}
