import { mount } from "svelte";

import "./app.css";
import App from "./App.svelte";

const alvo = document.getElementById("app");
if (!alvo) throw new Error("O elemento raiz da aplicação não foi encontrado.");

export default mount(App, { target: alvo });
