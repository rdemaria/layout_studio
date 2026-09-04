import { createRoot } from "react-dom/client";

import Home from "@source/app/page";
import "@source/app/globals.css";

const root = document.getElementById("root");

if (!root) throw new Error("Missing application root");

createRoot(root).render(<Home />);
