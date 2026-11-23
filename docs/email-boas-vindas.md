# E-mail de boas-vindas

Quem cria uma conta na ThyfIA recebe **um** e-mail de boas-vindas (no Design System: logo, Thyf, 3 primeiros passos,
planos com os preços do catálogo e aviso de que não há promessa de ganho). O envio é em segundo plano e nunca
atrapalha o cadastro; sem configuração, nada é enviado.

## Configurar (uma vez)

1. No Gmail da ThyfIA (`contato.thyfia@gmail.com`), ative a **verificação em duas etapas**
   (Conta Google > Segurança).
2. Em Conta Google > Segurança > **Senhas de app**, crie uma senha chamada "ThyfIA" e copie as 16 letras.
3. Na plataforma, como admin: **Admin > Configurações > E-mail de boas-vindas (Gmail)**.
   Preencha o e-mail remetente e a senha de app e salve.
4. Clique em **Enviar e-mail de teste para mim**. O e-mail chega no e-mail do seu usuário admin
   (confira também o spam na primeira vez).

O endereço público usado nos botões e links é o campo **Endereço público** (Geral); sem ele, usa
`https://thyfia.integradata.app.br`. Se o **WhatsApp de suporte** estiver preenchido, o e-mail ganha o link para ele.

## Detalhes

- Envio pelo SMTP do Gmail (`smtp.gmail.com:465`, SSL). O Gmail limita cerca de 500 e-mails por dia.
- Trava de segurança: no máximo 40 e-mails de boas-vindas por hora (o cadastro é aberto; sem a trava, alguém poderia
  usá-lo para mandar e-mail a terceiros). Passou disso, o cadastro segue e o e-mail é pulado, com aviso no log.
- A senha de app fica cifrada no banco, como os demais segredos.
- O modelo está em `thyfia/emails.py`; as imagens (logo e Thyf em PNG) em `thyfia/static/marca/email/` vão
  embutidas no e-mail, então aparecem sem pedir para "carregar imagens".

## Se o teste der erro

A mensagem vermelha agora traz a causa real entre parênteses (por exemplo `TimeoutError` ou `Network is unreachable`).

- **"O Gmail recusou o login"**: senha de app errada, ou o remetente não é a conta que gerou a senha. A senha pode ser
  colada com os espaços que o Google mostra; a ThyfIA tira os espaços.
- **"Não consegui conectar ao Gmail"**: a ThyfIA tenta a porta 465 (SSL) e, se não conectar, a 587 (STARTTLS). Se as
  duas falham, o servidor está bloqueando a saída de e-mail nessas portas (firewall da VPS ou do provedor).
- **"Falha na conexão segura"**: problema de certificado/TLS no servidor.
