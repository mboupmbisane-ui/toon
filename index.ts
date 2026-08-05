// Edge Function Supabase : "copernicus-token"
// Rôle : obtenir un token OAuth Copernicus (Sentinel-2) à la place du navigateur.
// Pourquoi : l'endpoint Keycloak de Copernicus (identity.dataspace.copernicus.eu)
// ne renvoie pas d'en-tête Access-Control-Allow-Origin, donc un appel fetch()
// direct depuis la page web est bloqué par le navigateur (erreur CORS), même
// avec un Client ID / Secret valides. En passant par cette fonction serveur,
// l'appel à Copernicus part du serveur (pas de CORS), et c'est la fonction qui
// répond au navigateur avec les bons en-têtes CORS.
//
// Déploiement (une seule fois) :
//   supabase functions deploy copernicus-token
//
// Le Client ID / Client Secret restent envoyés par le navigateur (comme avant),
// cette fonction ne fait que relayer la requête vers Copernicus — elle ne stocke
// et ne connaît aucun secret elle-même.

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const COPERNICUS_TOKEN_URL =
  "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token";

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Méthode non autorisée" }), {
      status: 405,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }

  try {
    const { client_id, client_secret } = await req.json();

    if (!client_id || !client_secret) {
      return new Response(
        JSON.stringify({ error: "client_id et client_secret sont requis" }),
        { status: 400, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
      );
    }

    const body = new URLSearchParams({
      grant_type: "client_credentials",
      client_id,
      client_secret,
    });

    const copernicusRes = await fetch(COPERNICUS_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });

    const data = await copernicusRes.json();

    if (!copernicusRes.ok) {
      return new Response(
        JSON.stringify({
          error:
            data.error_description ||
            data.error ||
            "Authentification Copernicus refusée (Client ID/Secret invalides).",
        }),
        {
          status: copernicusRes.status,
          headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
        }
      );
    }

    return new Response(
      JSON.stringify({ access_token: data.access_token, expires_in: data.expires_in }),
      { status: 200, headers: { ...CORS_HEADERS, "Content-Type": "application/json" } }
    );
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 500,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    });
  }
});
