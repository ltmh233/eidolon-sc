// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"context"
	"flag"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"eidolon/core/internal/catalog"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:8081", "private listen address")
	packs := flag.String("packs", "packs", "comma-separated package roots")
	legacy := flag.String("legacy", "http://127.0.0.1:8000", "private Python backend origin")
	flag.Parse()
	upstream, err := url.Parse(*legacy)
	if err != nil || upstream.Host == "" || upstream.Scheme != "http" || upstream.User != nil || upstream.RawQuery != "" || upstream.Fragment != "" || (upstream.Path != "" && upstream.Path != "/") {
		log.Fatal("legacy must be a private HTTP origin without credentials or path")
	}
	registry, err := catalog.Load(strings.Split(*packs, ",")...)
	if err != nil {
		log.Fatal(err)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil // local service calls must never go through ambient proxy settings
	transport.MaxIdleConnsPerHost = 16
	server := &http.Server{Addr: *listen, Handler: catalog.Handler(registry, upstream, transport), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32 << 10}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		<-ctx.Done()
		deadline, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		server.Shutdown(deadline)
	}()
	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("catalog listening on %s", listener.Addr())
	if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
	<-stopped // Serve returns before Shutdown finishes draining active responses.
}
