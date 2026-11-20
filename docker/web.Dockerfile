# The panel's built page (web/out) as an image, for an operator who takes the
# images from the registry instead of building them (IMAGES=registry in site.env):
# stack/deploy.sh copies /web out of it and ships it like a page built here.
#   docker build -f docker/web.Dockerfile -t awgscale-web:local web/out
FROM scratch
COPY . /web
