// Package circuitbreaker guards outbound calls (LLM, vector store, auth) so a
// flaky dependency does not cascade failures into every request.
//
// A breaker opens after CIRCUIT_BREAKER_THRESHOLD consecutive failures (default
// 5), rejects calls with ErrOpen for CIRCUIT_BREAKER_RESET_TIMEOUT seconds
// (default 30), then lets a single half-open probe through: success closes it,
// failure reopens it.
//
//	breakers := circuitbreaker.NewRegistry()
//	reply, err := circuitbreaker.Execute(breakers.Get("openai"), func() (Reply, error) {
//		return client.Chat(ctx, request)
//	})
package circuitbreaker

import (
	"errors"
	"os"
	"strconv"
	"sync"
	"time"
)

const (
	DefaultThreshold    = 5
	DefaultResetTimeout = 30 * time.Second
)

// ErrOpen is returned without calling the operation while the breaker is open.
var ErrOpen = errors.New("circuit breaker is open")

type State int

const (
	Closed State = iota
	Open
	HalfOpen
)

func (s State) String() string {
	switch s {
	case Open:
		return "open"
	case HalfOpen:
		return "half-open"
	default:
		return "closed"
	}
}

// Breaker is safe for concurrent use.
type Breaker struct {
	mu        sync.Mutex
	threshold int
	reset     time.Duration
	now       func() time.Time
	state     State
	failures  int
	openedAt  time.Time
}

// Option configures a Breaker.
type Option func(*Breaker)

// WithClock replaces the wall clock used for the reset timeout.
func WithClock(now func() time.Time) Option {
	return func(b *Breaker) { b.now = now }
}

// New builds a closed breaker; a threshold below 1 is treated as 1.
func New(threshold int, reset time.Duration, options ...Option) *Breaker {
	b := &Breaker{threshold: max(threshold, 1), reset: reset, now: time.Now}
	for _, option := range options {
		option(b)
	}
	return b
}

// State reports the current state; an open breaker past its reset timeout
// still reads as open until the next call probes it.
func (b *Breaker) State() State {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.state
}

// Call runs op unless the breaker is open. Errors and panics from op count
// as failures and are returned (or re-panicked) unchanged.
func (b *Breaker) Call(op func() error) error {
	probe, err := b.acquire()
	if err != nil {
		return err
	}
	failed := true
	defer func() { b.release(probe, failed) }()
	err = op()
	failed = err != nil
	return err
}

// Execute is Call for operations that return a value.
func Execute[T any](b *Breaker, op func() (T, error)) (T, error) {
	var value T
	err := b.Call(func() error {
		var err error
		value, err = op()
		return err
	})
	return value, err
}

func (b *Breaker) acquire() (probe bool, err error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	switch b.state {
	case HalfOpen:
		return false, ErrOpen
	case Open:
		if b.now().Sub(b.openedAt) < b.reset {
			return false, ErrOpen
		}
		b.state = HalfOpen
		return true, nil
	}
	return false, nil
}

func (b *Breaker) release(probe, failed bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if !probe && b.state != Closed {
		return
	}
	if !failed {
		b.state, b.failures = Closed, 0
		return
	}
	b.failures++
	if probe || b.failures >= b.threshold {
		b.state, b.openedAt = Open, b.now()
	}
}

// Registry hands out one named breaker per downstream dependency.
type Registry struct {
	mu        sync.Mutex
	threshold int
	reset     time.Duration
	options   []Option
	breakers  map[string]*Breaker
}

// NewRegistry reads CIRCUIT_BREAKER_THRESHOLD and CIRCUIT_BREAKER_RESET_TIMEOUT
// (seconds); unset or invalid values fall back to the defaults.
func NewRegistry(options ...Option) *Registry {
	return &Registry{
		threshold: envInt("CIRCUIT_BREAKER_THRESHOLD", DefaultThreshold),
		reset:     time.Duration(envInt("CIRCUIT_BREAKER_RESET_TIMEOUT", int(DefaultResetTimeout/time.Second))) * time.Second,
		options:   options,
		breakers:  map[string]*Breaker{},
	}
}

// Get returns the breaker for name, creating it on first use.
func (r *Registry) Get(name string) *Breaker {
	r.mu.Lock()
	defer r.mu.Unlock()
	breaker, ok := r.breakers[name]
	if !ok {
		breaker = New(r.threshold, r.reset, r.options...)
		r.breakers[name] = breaker
	}
	return breaker
}

func envInt(name string, fallback int) int {
	value, err := strconv.Atoi(os.Getenv(name))
	if err != nil || value <= 0 {
		return fallback
	}
	return value
}
