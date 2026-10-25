// Package models holds the item entity and its request payloads.
package models

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

type Item struct {
	ID          uuid.UUID       `json:"id"`
	Name        string          `json:"name"`
	Description *string         `json:"description"`
	Tags        json.RawMessage `json:"tags"`
	Status      string          `json:"status"`
	CustomerID  uuid.UUID       `json:"customer_id"`
	UserID      uuid.UUID       `json:"user_id"`
	CreatedAt   time.Time       `json:"created_at"`
	UpdatedAt   time.Time       `json:"updated_at"`
}

type CreateItem struct {
	Name        string          `json:"name"`
	Description *string         `json:"description"`
	Tags        json.RawMessage `json:"tags"`
	Status      *string         `json:"status"`
}

type UpdateItem struct {
	Name        *string         `json:"name"`
	Description *string         `json:"description"`
	Tags        json.RawMessage `json:"tags"`
	Status      *string         `json:"status"`
}

// Empty reports whether the patch changes nothing.
func (u UpdateItem) Empty() bool {
	return u.Name == nil && u.Description == nil && u.Tags == nil && u.Status == nil
}

type ListParams struct {
	Skip   int64
	Limit  int64
	Status string
	Search string
}

type Page[T any] struct {
	Items   []T   `json:"items"`
	Total   int64 `json:"total"`
	Skip    int64 `json:"skip"`
	Limit   int64 `json:"limit"`
	HasMore bool  `json:"has_more"`
}
