use tauri::{webview::NewWindowResponse, WebviewUrl, WebviewWindowBuilder};

const ERP_HOST: &str = "gestio-stock-web.onrender.com";

fn is_allowed_navigation(url: &tauri::Url) -> bool {
    let is_local_boot =
        matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost");
    let is_tauri_boot = url.scheme() == "tauri" && url.host_str() == Some("localhost");
    let is_official_erp = url.scheme() == "https"
        && url.host_str() == Some(ERP_HOST)
        && url.port_or_known_default() == Some(443)
        && url.username().is_empty()
        && url.password().is_none();

    is_local_boot || is_tauri_boot || is_official_erp
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("El Amine")
                .inner_size(1280.0, 820.0)
                .min_inner_size(1024.0, 700.0)
                .resizable(true)
                .maximizable(true)
                .on_navigation(is_allowed_navigation)
                .on_new_window(|_, _| NewWindowResponse::Deny)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("échec du démarrage de l'application El Amine");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn url(value: &str) -> tauri::Url {
        value.parse().expect("URL de test valide")
    }

    #[test]
    fn official_https_origin_is_allowed() {
        assert!(is_allowed_navigation(&url(
            "https://gestio-stock-web.onrender.com/"
        )));
        assert!(is_allowed_navigation(&url(
            "https://gestio-stock-web.onrender.com/inventory/products/?page=2"
        )));
        assert!(is_allowed_navigation(&url(
            "https://gestio-stock-web.onrender.com/sales/#invoice-42"
        )));
    }

    #[test]
    fn local_boot_page_is_allowed() {
        assert!(is_allowed_navigation(&url(
            "http://tauri.localhost/index.html"
        )));
        assert!(is_allowed_navigation(&url("tauri://localhost/index.html")));
    }

    #[test]
    fn insecure_or_untrusted_origins_are_blocked() {
        assert!(!is_allowed_navigation(&url(
            "http://gestio-stock-web.onrender.com/"
        )));
        assert!(!is_allowed_navigation(&url("https://example.com/")));
        assert!(!is_allowed_navigation(&url(
            "https://gestio-stock-web.onrender.com.attacker.invalid/"
        )));
        assert!(!is_allowed_navigation(&url(
            "https://sub.gestio-stock-web.onrender.com/"
        )));
        assert!(!is_allowed_navigation(&url(
            "https://gestio-stock-web.onrender.com:444/"
        )));
        assert!(!is_allowed_navigation(&url(
            "https://user:password@gestio-stock-web.onrender.com/"
        )));
        assert!(!is_allowed_navigation(&url(
            "file:///C:/Windows/System32/config/SAM"
        )));
        assert!(!is_allowed_navigation(&url("javascript:alert(1)")));
    }

    #[test]
    fn bootstrap_contains_no_tauri_bridge_or_embedded_secret() {
        let script = include_str!("../../web/app.js");
        let page = include_str!("../../web/index.html");

        assert!(script.contains("https://gestio-stock-web.onrender.com/"));
        assert!(!script.contains("__TAURI__"));
        assert!(!script.contains("invoke("));
        assert!(!script.to_ascii_lowercase().contains("api_key"));
        assert!(!script.to_ascii_lowercase().contains("password"));
        assert!(page.contains("<script defer src=\"app.js\"></script>"));
        assert!(!page.contains("<script>"));
    }
}
