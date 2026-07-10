import { rmSync } from "node:fs";

rmSync("frontend/build", { recursive: true, force: true });
rmSync("ui/static/dist", { recursive: true, force: true });
