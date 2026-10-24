// Package apperrors implements the RFC-007 error contract.
//
// Every HTTP error response is serialized into the canonical envelope:
//
//	{"error": {"code": "NOT_FOUND", "message": "Item 'abc' not found",
//	           "type": "NotFoundError", "context": {}, "correlation_id": ""}}
//
// The authoritative correlation id travels in the X-Request-Id response header.
package apperrors

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// Code is a canonical error code shared across the Python / Node / Rust / Go backends.
type Code string

const (
	AuthRequired          Code = "AUTH_REQUIRED"
	PermissionDenied      Code = "PERMISSION_DENIED"
	ReadOnly              Code = "READ_ONLY"
	NotFound              Code = "NOT_FOUND"
	AlreadyExists         Code = "ALREADY_EXISTS"
	DuplicateEntry        Code = "DUPLICATE_ENTRY"
	ForeignKeyViolation   Code = "FOREIGN_KEY_VIOLATION"
	ConstraintViolation   Code = "CONSTRAINT_VIOLATION"
	ValidationFailed      Code = "VALIDATION_FAILED"
	InvalidInput          Code = "INVALID_INPUT"
	RateLimited           Code = "RATE_LIMITED"
	InternalError         Code = "INTERNAL_ERROR"
	DatabaseUnavailable   Code = "DATABASE_UNAVAILABLE"
	DatabaseTimeout       Code = "DATABASE_TIMEOUT"
	DependencyUnavailable Code = "DEPENDENCY_UNAVAILABLE"
)

var codeInfo = map[Code]struct {
	status   int
	typeName string
}{
	AuthRequired:          {http.StatusUnauthorized, "AuthRequiredError"},
	PermissionDenied:      {http.StatusForbidden, "PermissionDeniedError"},
	ReadOnly:              {http.StatusForbidden, "ReadOnlyError"},
	NotFound:              {http.StatusNotFound, "NotFoundError"},
	AlreadyExists:         {http.StatusConflict, "AlreadyExistsError"},
	DuplicateEntry:        {http.StatusConflict, "DuplicateEntryError"},
	ForeignKeyViolation:   {http.StatusConflict, "ForeignKeyViolationError"},
	ConstraintViolation:   {http.StatusConflict, "ConstraintViolationError"},
	ValidationFailed:      {http.StatusUnprocessableEntity, "ValidationError"},
	InvalidInput:          {http.StatusUnprocessableEntity, "InvalidInputError"},
	RateLimited:           {http.StatusTooManyRequests, "RateLimitedError"},
	InternalError:         {http.StatusInternalServerError, "InternalError"},
	DatabaseUnavailable:   {http.StatusServiceUnavailable, "DatabaseUnavailableError"},
	DatabaseTimeout:       {http.StatusServiceUnavailable, "DatabaseTimeoutError"},
	DependencyUnavailable: {http.StatusServiceUnavailable, "DependencyUnavailableError"},
}

// Status returns the HTTP status for a code.
func (c Code) Status() int { return codeInfo[c].status }

// TypeName returns the RFC-007 error type for a code.
func (c Code) TypeName() string { return codeInfo[c].typeName }

// Error is an application error carrying its public contract.
type Error struct {
	Code    Code
	Message string
	Context map[string]any
	cause   error
}

func (e *Error) Error() string { return e.Message }

func (e *Error) Unwrap() error { return e.cause }

// PublicMessage hides internal details from clients.
func (e *Error) PublicMessage() string {
	if e.Code == InternalError {
		return "Internal server error"
	}
	return e.Message
}

func New(code Code, message string) *Error {
	return &Error{Code: code, Message: message, Context: map[string]any{}}
}

func NewNotFound(entity, id string) *Error {
	err := New(NotFound, fmt.Sprintf("%s '%s' not found", entity, id))
	err.Context = map[string]any{"entity": entity, "id": id}
	return err
}

func NewAlreadyExists(entity, name string) *Error {
	err := New(AlreadyExists, fmt.Sprintf("%s '%s' already exists", entity, name))
	err.Context = map[string]any{"entity": entity, "name": name}
	return err
}

func NewDuplicateEntry(entity, field, value string) *Error {
	err := New(DuplicateEntry, fmt.Sprintf("duplicate %s: %s='%s' already exists", entity, field, value))
	err.Context = map[string]any{"entity": entity, "field": field, "value": value}
	return err
}

func NewReadOnly(resource string) *Error {
	err := New(ReadOnly, resource+" is read-only")
	err.Context = map[string]any{"resource": resource}
	return err
}

func NewDependencyUnavailable(dependency string) *Error {
	err := New(DependencyUnavailable, dependency+" is unavailable")
	err.Context = map[string]any{"dependency": dependency}
	return err
}

func NewValidation(message string) *Error { return New(ValidationFailed, message) }

func NewInternal(cause error) *Error {
	err := New(InternalError, "internal error")
	err.cause = cause
	return err
}

// FromDatabase maps driver failures onto the public contract.
func FromDatabase(err error) *Error {
	var pgErr *pgconn.PgError
	var connectErr *pgconn.ConnectError
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		return NewNotFound("Row", "unknown")
	case errors.Is(err, context.DeadlineExceeded), pgconn.Timeout(err):
		return New(DatabaseTimeout, "database operation timed out")
	case errors.As(err, &connectErr):
		return New(DatabaseUnavailable, "database is unavailable")
	case errors.As(err, &pgErr):
		switch {
		case pgErr.Code == "23505":
			return New(DuplicateEntry, "duplicate entry")
		case pgErr.Code == "23503":
			return New(ForeignKeyViolation, "foreign key violation")
		case strings.HasPrefix(pgErr.Code, "23"):
			return New(ConstraintViolation, "constraint violation")
		}
	}
	return NewInternal(err)
}

// As converts any error into an *Error, defaulting to INTERNAL_ERROR.
func As(err error) *Error {
	var appErr *Error
	if errors.As(err, &appErr) {
		return appErr
	}
	return NewInternal(err)
}

// Envelope is the RFC-007 response body.
type Envelope struct {
	Error Body `json:"error"`
}

// Body is the inner RFC-007 error object.
type Body struct {
	Code          Code           `json:"code"`
	Message       string         `json:"message"`
	Type          string         `json:"type"`
	Context       map[string]any `json:"context"`
	CorrelationID string         `json:"correlation_id"`
}

// Serialize builds the envelope for err. The correlation id is left empty;
// Write fills it from the response.
func Serialize(err error) Envelope {
	appErr := As(err)
	details := appErr.Context
	if details == nil {
		details = map[string]any{}
	}
	return Envelope{Error: Body{
		Code:    appErr.Code,
		Message: appErr.PublicMessage(),
		Type:    appErr.Code.TypeName(),
		Context: details,
	}}
}

var serializer = Serialize

// UseSerializer routes Write through fn, the error_port adapter seam. Call it
// before serving; nil restores Serialize.
func UseSerializer(fn func(error) Envelope) {
	if fn == nil {
		fn = Serialize
	}
	serializer = fn
}

// Write serializes err into the RFC-007 envelope. The HTTP status follows the
// envelope code; codes without a mapping answer 500.
func Write(w http.ResponseWriter, r *http.Request, err error) {
	envelope := serializer(err)
	status := envelope.Error.Code.Status()
	if status == 0 {
		status = http.StatusInternalServerError
	}
	if status >= http.StatusInternalServerError {
		slog.ErrorContext(r.Context(), "request failed", "error", err, "code", envelope.Error.Code)
	} else {
		slog.WarnContext(r.Context(), "request failed", "error", err, "code", envelope.Error.Code)
	}
	envelope.Error.CorrelationID = w.Header().Get("X-Request-Id")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(envelope)
}
