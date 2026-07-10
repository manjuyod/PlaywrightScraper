import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";


test("built dashboard is local and has no student credential management", () => {
    const assets = readFileSync("ui/templates/_react_assets.html", "utf8");
    const bundle = readFileSync("ui/static/dist/dashboard.js", "utf8");
    assert.match(assets, /dist\/dashboard\.css/);
    assert.match(assets, /dist\/dashboard\.js/);
    assert.doesNotMatch(assets, /https?:\/\//);
    for (const forbidden of [
        "Add Student",
        "edit_student",
        "add_student",
        "delete_students",
        "portal_password",
    ]) {
        assert.equal(bundle.includes(forbidden), false, forbidden);
    }
});
