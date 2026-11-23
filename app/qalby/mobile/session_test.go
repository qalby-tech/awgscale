//go:build linux

package mobile

import (
	"testing"
	"time"
)

func TestUpOnlyWhileTheHandshakeIsFresh(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	cases := []struct {
		name      string
		handshake int64
		want      State
	}{
		{"no handshake yet", 0, Connecting},
		{"a handshake just now", now.Unix(), Up},
		{"a handshake a minute ago", now.Add(-time.Minute).Unix(), Up},
		{"one second short of the limit", now.Add(-handshakeFresh + time.Second).Unix(), Up},
		{"exactly at the limit", now.Add(-handshakeFresh).Unix(), Connecting},
		{"older than the limit", now.Add(-handshakeFresh - time.Minute).Unix(), Connecting},
	}
	for _, each := range cases {
		if got := stateOf(each.handshake, now); got != each.want {
			t.Errorf("%s: %s, want %s", each.name, got, each.want)
		}
	}
}

func TestFreshnessIsThreeMinutesUnlessSet(t *testing.T) {
	t.Setenv("AWGSCALE_HANDSHAKE_FRESH", "")
	if freshness() != 180*time.Second {
		t.Fatalf("default: %s", freshness())
	}
	t.Setenv("AWGSCALE_HANDSHAKE_FRESH", "30")
	if freshness() != 30*time.Second {
		t.Fatalf("set to 30: %s", freshness())
	}
	for _, wrong := range []string{"0", "-5", "soon"} {
		t.Setenv("AWGSCALE_HANDSHAKE_FRESH", wrong)
		if freshness() != 180*time.Second {
			t.Fatalf("%q: %s", wrong, freshness())
		}
	}
}
