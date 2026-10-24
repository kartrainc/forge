// Package worker is the background-job queue, powered by asynq + Redis.
//
// Define a job as a payload type plus a handler and register the handler in
// Mux; cmd/worker drains the queue. Enqueue from request handlers:
//
//	client, err := worker.NewClient(worker.BrokerURL())
//	task, err := worker.NewHelloTask(worker.HelloJob{Name: "forge"})
//	_, err = client.EnqueueContext(ctx, task)
package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"

	"github.com/hibiken/asynq"
)

const (
	// Queue is the asynq queue the worker drains.
	Queue = "forge-tasks"
	// TypeHello names the example job.
	TypeHello = "hello"
	// DefaultBrokerURL is used when TASKIQ_BROKER_URL is unset; the env var
	// name is shared with the Python, Node and Rust workers.
	DefaultBrokerURL = "redis://redis:6379/2"
)

// BrokerURL returns TASKIQ_BROKER_URL or the compose default.
func BrokerURL() string {
	if url := os.Getenv("TASKIQ_BROKER_URL"); url != "" {
		return url
	}
	return DefaultBrokerURL
}

// NewClient returns a producer for the queue at url.
func NewClient(url string) (*asynq.Client, error) {
	redis, err := asynq.ParseRedisURI(url)
	if err != nil {
		return nil, err
	}
	return asynq.NewClient(redis), nil
}

// HelloJob is the example job payload.
type HelloJob struct {
	Name string `json:"name"`
}

// NewHelloTask builds a hello task on the worker queue.
func NewHelloTask(job HelloJob) (*asynq.Task, error) {
	payload, err := json.Marshal(job)
	if err != nil {
		return nil, err
	}
	return asynq.NewTask(TypeHello, payload, asynq.Queue(Queue)), nil
}

// HandleHello is the example handler; replace or extend with project work.
func HandleHello(ctx context.Context, task *asynq.Task) error {
	var job HelloJob
	if err := json.Unmarshal(task.Payload(), &job); err != nil {
		return fmt.Errorf("decode hello job: %w: %w", err, asynq.SkipRetry)
	}
	slog.InfoContext(ctx, "hello_job executed", "name", job.Name)
	return nil
}

// Mux routes task types to handlers.
func Mux() *asynq.ServeMux {
	mux := asynq.NewServeMux()
	mux.HandleFunc(TypeHello, HandleHello)
	return mux
}

// Run processes jobs until ctx is cancelled, then waits for in-flight jobs.
// An unreachable broker fails at startup instead of retrying silently.
func Run(ctx context.Context, url string) error {
	redis, err := asynq.ParseRedisURI(url)
	if err != nil {
		return fmt.Errorf("invalid broker url: %w", err)
	}
	server := asynq.NewServer(redis, asynq.Config{
		Queues:   map[string]int{Queue: 1},
		Logger:   logger{},
		LogLevel: asynq.WarnLevel,
	})
	if err := server.Ping(); err != nil {
		return fmt.Errorf("broker unreachable: %w", err)
	}
	if err := server.Start(Mux()); err != nil {
		return err
	}
	slog.Info("worker started; awaiting jobs", "queue", Queue)
	<-ctx.Done()
	slog.Info("shutdown signal received — draining jobs")
	server.Shutdown()
	return nil
}

// logger routes asynq's internal logs through slog.
type logger struct{}

func (logger) Debug(args ...any) { slog.Debug(fmt.Sprint(args...), "component", "asynq") }
func (logger) Info(args ...any)  { slog.Info(fmt.Sprint(args...), "component", "asynq") }
func (logger) Warn(args ...any)  { slog.Warn(fmt.Sprint(args...), "component", "asynq") }
func (logger) Error(args ...any) { slog.Error(fmt.Sprint(args...), "component", "asynq") }
func (logger) Fatal(args ...any) {
	slog.Error(fmt.Sprint(args...), "component", "asynq")
	os.Exit(1)
}
