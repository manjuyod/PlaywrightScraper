const React = require("react");
const ReactDOM = require("react-dom/client");

globalThis.React = React;
globalThis.ReactDOM = ReactDOM;

require("../build/dashboard.runtime.js");
