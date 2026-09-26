# Insere `local_certs` no bloco global do Caddyfile de produção, sem editá-lo.
#
# Existe para a validação da stack: o domínio de teste não tem DNS público,
# então o Let's Encrypt não conseguiria emitir certificado nenhum e o Caddy
# ficaria sem servir HTTPS. Com `local_certs` ele assina com a CA interna dele,
# e o que se valida passa a ser o que importa aqui — o roteamento do Caddyfile
# de verdade, o mesmo arquivo que vai para produção.
#
# O que isto NÃO valida, e nenhum teste local valida: a emissão pelo Let's
# Encrypt, que depende de DNS público apontando para o servidor.
BEGIN { feito = 0 }
/^\{$/ && feito == 0 { print "{"; print "\tlocal_certs"; feito = 1; next }
{ print }
