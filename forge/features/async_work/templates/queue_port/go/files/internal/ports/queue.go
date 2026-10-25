// Package ports declares the capability contracts that adapters implement.
package ports

import (
	"context"
	"errors"
	"iter"
	"time"
)

// QueueMessage is one message as delivered by a consumer.
type QueueMessage struct {
	// ID is the provider-assigned message id.
	ID string
	// Body is the decoded JSON payload.
	Body map[string]any
	// Receipt is the opaque handle passed back to Ack or Nack.
	Receipt string
}

var (
	// ErrQueueTransport wraps broker failures (Redis disconnect, API errors).
	ErrQueueTransport = errors.New("queue transport error")
	// ErrQueueSerialization wraps payloads that cannot be encoded or decoded.
	ErrQueueSerialization = errors.New("queue serialization error")
	// ErrQueueUnknownReceipt is returned when Ack or Nack names a receipt
	// that is not in flight.
	ErrQueueUnknownReceipt = errors.New("queue receipt not in flight")
)

// QueuePort is the queue contract (RFC-012, same shape as the Python, Node
// and Rust ports). Adapters live in internal/adapters/queue_*.go; the rest of
// the service depends on this interface, not on an adapter type.
type QueuePort interface {
	// Enqueue stores one message and returns its id. A zero delay delivers
	// immediately.
	Enqueue(ctx context.Context, topic string, body map[string]any, delay time.Duration) (string, error)
	// Consume yields messages from topic until ctx ends or the caller stops
	// ranging. Every yielded message must be acked or nacked; stopping
	// early redelivers the un-acked ones.
	Consume(ctx context.Context, topic string, batchSize int) iter.Seq2[QueueMessage, error]
	// Ack removes a message from the queue.
	Ack(ctx context.Context, topic, receipt string) error
	// Nack rejects a message: requeue retries it, otherwise it goes to the
	// dead-letter store.
	Nack(ctx context.Context, topic, receipt string, requeue bool) error
}
