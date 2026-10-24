package routes

import (
	"net/http"

	"github.com/go-chi/chi/v5"
)

func registerHome(router chi.Router, info Info) {
	router.Get("/", handle(func(w http.ResponseWriter, _ *http.Request) error {
		return writeJSON(w, http.StatusOK, map[string]string{"message": "Welcome to the API"})
	}))
	router.Get("/info", handle(func(w http.ResponseWriter, _ *http.Request) error {
		return writeJSON(w, http.StatusOK, info)
	}))
}
