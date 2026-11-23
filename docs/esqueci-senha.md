# Esqueci minha senha

Fluxo de recuperação de senha por e-mail, usando o mesmo envio de e-mail da plataforma (Admin > Configurações > **E-mails da
plataforma**, veja `docs/email-boas-vindas.md`). Sem essa configuração o botão funciona na tela, mas nenhum e-mail sai e o aviso fica no log.

## Como funciona para o cliente

1. Na tela de entrada, **Esqueci minha senha** (abaixo do campo de senha). O e-mail digitado no login já vem preenchido.
2. A tela sempre responde "confira seu e-mail", exista a conta ou não. Assim ninguém descobre quais e-mails têm conta.
3. O e-mail traz o botão **Criar nova senha**, que abre `https://thyfia.integradata.app.br/?redefinir=TOKEN`.
4. O cliente digita a nova senha duas vezes e volta para a tela de entrada com o e-mail preenchido.

## Segurança

- O token tem 256 bits, vale **60 minutos** e funciona **uma única vez**. No banco fica só o hash SHA-256
  (tabela `redefinicoes_senha`); o token em si só existe no e-mail.
- Pedir um novo link invalida os anteriores da mesma conta.
- Trocar a senha gera novo sal e **encerra todas as sessões** da conta (quem estiver logado, inclusive um invasor, cai).
- Limites: 3 pedidos por hora por conta (evita encher a caixa de entrada de alguém) e 60 e-mails de redefinição por hora
  no total. O limite das boas-vindas é separado.
- Ao abrir o link, o token sai da barra de endereço na hora e não fica no histórico.
- Contas desativadas não recebem link.

## Arquivos

- `thyfia/auth.py`: `pedir_redefinicao`, `redefinir_senha`.
- `thyfia/emails.py`: `montar_mensagem_redefinicao`, `enviar_redefinicao` (nunca levanta erro).
- `thyfia/app.py`: `POST /api/senha/esqueci` e `POST /api/senha/redefinir`.
- Front: `index.html` (`#form-esqueci`, `#form-redefinir`), `app.js` (`modoAuth`), `styles.css` (`.auth-solo`).
