package tunnel

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// The service's own record of what it was asked and what came of it: up, down, failures, and the
// library's errors. Never a key or a config. On Windows a service has no standard error anyone
// reads, so it goes to a file (EventLogPath); elsewhere to standard error (the journal of systemd).

// eventLogLimit: a log grown beyond this is started anew when the service starts.
const eventLogLimit = 1 << 20

var events = struct {
	sync.Mutex
	out io.Writer
}{out: os.Stderr}

// LogTo sends the record to a file, kept small.
func LogTo(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	flags := os.O_CREATE | os.O_APPEND | os.O_WRONLY
	if info, err := os.Stat(path); err == nil && info.Size() > eventLogLimit {
		flags = os.O_CREATE | os.O_TRUNC | os.O_WRONLY
	}
	file, err := os.OpenFile(path, flags, 0o644)
	if err != nil {
		return err
	}
	events.Lock()
	events.out = file
	events.Unlock()
	return nil
}

// Event writes one line of the record.
func Event(format string, arguments ...any) {
	events.Lock()
	defer events.Unlock()
	fmt.Fprintf(events.out, time.Now().Format("2006-01-02 15:04:05.000 ")+format+"\n", arguments...)
}
