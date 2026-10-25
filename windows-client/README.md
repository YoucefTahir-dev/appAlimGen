# El Amine pour Windows

Client Windows Tauri v2 qui ouvre l'ERP Django officiel dans WebView2.

Le projet ne contient ni mot de passe, ni jeton, ni secret. La page locale sert
uniquement d'écran de démarrage et de reprise hors connexion. Elle redirige
ensuite vers `https://gestio-stock-web.onrender.com/`.

## Construction

Prérequis : Rust MSVC, Microsoft C++ Build Tools et WebView2.

```powershell
cd windows-client\src-tauri
cargo test
cargo install tauri-cli --version "^2" --locked
cargo tauri build --bundles nsis
```

L'installateur est produit sous
`src-tauri\target\release\bundle\nsis\`.
