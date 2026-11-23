// For app/ios/check.sh: renders app/ios/project.yml (a Handlebars template) roughly the way `tauri ios init`
// does, with the values it would pass for this app (tauri-cli 2.12.6, src/mobile/ios/project.rs) and
// stand-ins for its helpers, so that XcodeGen can be tried on the result. The real rendering is CI's.
const fs = require("fs");
const path = require("path");
const Handlebars = require("handlebars");
Handlebars.registerHelper("join", list => (list || []).join(", "));
Handlebars.registerHelper("prefix-path", path => path);
Handlebars.registerHelper("shell-escape", text => text);
// the app's version, as init takes it from app/shell/tauri.conf.json
const version = JSON.parse(fs.readFileSync(path.join(__dirname, "../../shell/tauri.conf.json"), "utf8")).version;
const values = {
  app: { name: "awgscale-app", identifier: "tech.qalby.awgscale", "stylized-name": "awgscale", "asset-dir": "assets" },
  apple: { "ios-version": "15.0", "bundle-version-short": version, "bundle-version": version, "plist-pairs": [] },
  "file-groups": [], "asset-catalogs": [], "ios-additional-targets": [], "ios-valid-archs": ["arm64"],
  "lib-output-file-name": "libapp.a", "ios-libraries": [], "ios-pre-build-scripts": [],
};
const template = Handlebars.compile(fs.readFileSync(process.argv[2], "utf8"), { noEscape: true });
fs.writeFileSync(process.argv[3], template(values));
