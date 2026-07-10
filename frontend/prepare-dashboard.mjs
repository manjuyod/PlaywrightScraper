import { readFileSync, writeFileSync } from "node:fs";

const sourcePath = "ui/static/react-dashboard.js";
const outputPath = "frontend/build/dashboard.runtime.js";
const source = readFileSync(sourcePath, "utf8");
const startMarker = "    function appendQuery(";
const endMarker = "    function FranchisePage(";
const start = source.indexOf(startMarker);
const end = source.indexOf(endMarker);

if (start < 0 || end <= start) {
  throw new Error("Dashboard student-management block markers were not found");
}

const runtime = source.slice(0, start) + source.slice(end);
for (const forbidden of [
  "StudentDialog",
  "edit_student",
  "add_student",
  "delete_students",
  "portal_password"
]) {
  if (runtime.includes(forbidden)) {
    throw new Error(`Read-only dashboard bundle still contains ${forbidden}`);
  }
}
writeFileSync(outputPath, runtime, "utf8");
