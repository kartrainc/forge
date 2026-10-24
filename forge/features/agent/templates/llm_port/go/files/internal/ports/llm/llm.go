// Package llm is the LLM provider port: streaming chat completions and
// embeddings behind one interface.
//
// It mirrors the TypeSpec contract at forge/templates/_shared/ports/llm/contract.tsp
// and the Python, Node and Rust ports (JSON field names match the contract).
// Adapters live under internal/adapters; the rest of the service depends on
// Port, never on an adapter type.
package llm

import (
	"context"
	"fmt"
	"iter"
)

// Role is the speaker of a chat message.
type Role string

const (
	RoleSystem    Role = "system"
	RoleUser      Role = "user"
	RoleAssistant Role = "assistant"
	RoleTool      Role = "tool"
)

// Tool declares a function the model may call. InputSchema is a JSON Schema
// document describing the arguments.
type Tool struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"inputSchema"`
}

// Message is one turn of the conversation. ToolCalls carries the calls an
// assistant turn emitted; ToolCallID links a tool turn to one of them.
type Message struct {
	Role       Role             `json:"role"`
	Content    string           `json:"content"`
	ToolCalls  []map[string]any `json:"toolCalls,omitempty"`
	Name       string           `json:"name,omitempty"`
	ToolCallID string           `json:"toolCallId,omitempty"`
}

// Prompt is the ordered message history plus the tools the model may call.
type Prompt struct {
	Messages []Message `json:"messages"`
	Tools    []Tool    `json:"tools,omitempty"`
}

// Options tunes one Complete call; nil fields use the provider default.
type Options struct {
	ModelID     string   `json:"modelId"`
	Temperature *float64 `json:"temperature,omitempty"`
	MaxTokens   *int64   `json:"maxTokens,omitempty"`
}

// ToolCallChunk advances an in-flight tool call. ID correlates the deltas of
// one call when the model streams several calls in parallel.
type ToolCallChunk struct {
	ID             string `json:"id,omitempty"`
	Name           string `json:"name,omitempty"`
	ArgumentsDelta string `json:"argumentsDelta,omitempty"`
}

// Chunk is one streamed completion delta. The terminal chunk carries
// FinishReason.
type Chunk struct {
	Delta        string         `json:"delta"`
	FinishReason string         `json:"finishReason,omitempty"`
	ToolCall     *ToolCallChunk `json:"toolCall,omitempty"`
}

// Port is the LLM capability every adapter implements.
type Port interface {
	// Complete streams a chat completion, yielding chunks as they arrive. A
	// failure is yielded once, as the last element, with an *Error.
	Complete(ctx context.Context, prompt Prompt, options Options) iter.Seq2[Chunk, error]
	// Embed returns one vector per input text, in input order.
	Embed(ctx context.Context, texts []string) ([][]float32, error)
}

// ErrorKind classifies a port failure.
type ErrorKind string

const (
	// KindTransport is a network or I/O failure before or during the call.
	KindTransport ErrorKind = "transport"
	// KindProvider is a provider rejection (auth, quota, invalid model, ...).
	KindProvider ErrorKind = "provider"
	// KindSerialization is a request or response that cannot be encoded.
	KindSerialization ErrorKind = "serialization"
)

// Error is returned by every Port operation. StatusCode is the provider HTTP
// status for KindProvider errors, 0 otherwise.
type Error struct {
	Kind       ErrorKind
	StatusCode int
	Err        error
}

func (e *Error) Error() string {
	return fmt.Sprintf("llm %s error: %v", e.Kind, e.Err)
}

func (e *Error) Unwrap() error {
	return e.Err
}
