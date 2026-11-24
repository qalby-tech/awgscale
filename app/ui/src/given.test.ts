// What a scanned, pasted or opened text is taken for on the join screen.
import { describe, expect, it } from "vitest";

import { Given, givenKind } from "./given";

describe("givenKind", () => {
  it("takes awgscale's invitations, either form", () => {
    expect(givenKind("awgscale://join?h=vpn.example.org&c=Zx3kQ0aB1cD2eF3gH4iJ5k")).toBe(Given.Invitation);
    expect(givenKind("  https://vpn.example.org/i/Zx3kQ0aB1cD2eF3gH4iJ5k\n")).toBe(Given.Invitation);
    expect(givenKind("AWGSCALE://join?h=vpn.example.org&c=Zx3kQ0aB1cD2eF3gH4iJ5k")).toBe(Given.Invitation);
  });

  it("takes an Amnezia key, its QR frame and a config", () => {
    expect(givenKind("vpn://AAAGZ3iclVRtU-IwEP4rTD5zXJO2tDB6M55wIiqi4J0vdZi0jRopaU0C")).toBe(Given.Connection);
    expect(givenKind("B8ABAAAAAyAAAAZneJyVVG1T4jAQ_itMPnNck7a0MHoznnAiKqLgnS91mLSNGilpTQL4Mv")).toBe(Given.Connection);
    expect(givenKind("[Interface]\nPrivateKey = made-up\nAddress = 10.8.1.2/32\n\n[Peer]\n")).toBe(Given.Connection);
    expect(givenKind("# from the server\n[interface]\nAddress = 10.8.1.2/32\n")).toBe(Given.Connection);
  });

  it("takes nothing else", () => {
    for (const text of ["", "hello", "http://vpn.example.org/i/x", "WIFI:S:home;T:WPA;P:secret;;", "B8 with spaces"]) {
      expect(givenKind(text)).toBe(Given.Nothing);
    }
  });
});
