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

	"eidolon/core/internal/affinity"
	"eidolon/core/internal/catalog"
	"eidolon/core/internal/stats"
	"eidolon/core/internal/timeline"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:8081", "private listen address")
	packs := flag.String("packs", "packs", "comma-separated package roots")
	legacy := flag.String("legacy", "http://127.0.0.1:8000", "private Python backend origin")
	affinityToken := flag.String("affinity-token-file", "", "owner-only service credential file; enables Go affinity reads")
	statsToken := flag.String("stats-token-file", "", "owner-only core service credential file; enables Go statistics reads")
	timelineToken := flag.String("timeline-token-file", "", "owner-only core service credential file; enables Go history projection")
	scWorld := flag.String("sc-world", "", "reviewed SC world.json for offline export")
	export := flag.String("export-worlds", "", "write private world snapshot atomically, then exit (no listener)")
	characters := flag.String("sc-characters", "", "include SC characters TOML in a unified v2 snapshot")
	snapshot := flag.String("content-snapshot", "", "private v2 snapshot for native static character catalogs")
	withGifts := flag.Bool("with-gifts", false, "export v3 unified static content, including gifts")
	display := flag.String("catalog-display", "", "published SC display metadata bound to content-snapshot")
	flag.Parse()
	if *withGifts && (*export == "" || *characters == "") {
		log.Fatal("-with-gifts requires character export")
	}
	upstream, err := url.Parse(*legacy)
	if err != nil || upstream.Host == "" || upstream.Scheme != "http" || upstream.User != nil || upstream.RawQuery != "" || upstream.Fragment != "" || (upstream.Path != "" && upstream.Path != "/") {
		log.Fatal("legacy must be a private HTTP origin without credentials or path")
	}
	registry, err := catalog.Load(strings.Split(*packs, ",")...)
	if err != nil {
		log.Fatal(err)
	}
	if *export != "" {
		if *scWorld == "" {
			log.Fatal("-export-worlds requires -sc-world")
		}
		var err error
		if *characters != "" {
			err = catalog.ExportRelease(registry, *characters, *scWorld, *export, *withGifts)
		} else {
			err = catalog.ExportWorlds(registry, *scWorld, *export)
		}
		if err != nil {
			log.Fatal(err)
		}
		return
	}
	if *scWorld != "" || *characters != "" {
		log.Fatal("-sc-world requires -export-worlds; private content is not served over HTTP")
	}
	if *snapshot != "" {
		if err := catalog.AttachContent(registry, *snapshot); err != nil {
			log.Fatal(err)
		}
	}
	if *display != "" {
		if *snapshot == "" {
			log.Fatal("catalog-display requires content-snapshot")
		}
		if err := catalog.AttachDisplay(registry, *display); err != nil {
			log.Fatal(err)
		}
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil // local service calls must never go through ambient proxy settings
	transport.MaxIdleConnsPerHost = 16
	var affinityRead http.Handler
	if *affinityToken != "" {
		token, err := affinity.ReadTokenFile(*affinityToken)
		if err != nil {
			log.Fatal(err)
		}
		affinityRead = affinity.NewHandler(upstream, transport, token)
	}
	var statsRead http.Handler
	if *statsToken != "" {
		token, err := affinity.ReadTokenFile(*statsToken)
		if err != nil {
			log.Fatal(err)
		}
		statsRead = stats.NewHandler(upstream, transport, token)
	}
	var timelineRead http.Handler
	if *timelineToken != "" {
		token, err := affinity.ReadTokenFile(*timelineToken)
		if err != nil {
			log.Fatal(err)
		}
		timelineRead = timeline.NewHandler(upstream, transport, token)
	}
	server := &http.Server{Addr: *listen, Handler: catalog.Handler(registry, upstream, transport, affinityRead, statsRead, timelineRead), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32 << 10}
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
