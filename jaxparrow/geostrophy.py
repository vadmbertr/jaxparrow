from jax import jit
import jax.numpy as jnp
from jaxtyping import Array, Float

from .tools import geometry, operators, sanitize
from .tools.stencil import stencil


# =============================================================================
# Geostrophy
# =============================================================================

def geostrophy(
        ssh_t: Float[Array, "lat lon"],
        lat_t: Float[Array, "lat lon"],
        lon_t: Float[Array, "lat lon"],
        mask: Float[Array, "lat lon"] = None,
        return_grids: bool = True,
        stencil_width: int = stencil.STENCIL_WIDTH
) -> [Float[Array, "lat lon"], ...]:
    """
    Computes the geostrophic Sea Surface Current (SSC) velocity field from a Sea Surface Height (SSH) field.

    The geostrophic SSC velocity field is computed on a C-grid, following NEMO convention [1]_.

    Parameters
    ----------
    ssh_t : Float[Array, "lat lon"]
        SSH field (on the T grid)
    lat_t : Float[Array, "lat lon"]
        Latitudes of the T grid
    lon_t : Float[Array, "lat lon"]
        Longitudes of the T grid
    mask : Float[Array, "lat lon"], optional
        Mask defining the marine area of the spatial domain; `1` or `True` stands for masked (i.e. land)

        If not provided, inferred from ``ssh_t`` `nan` values
    return_grids : bool, optional
        If `True`, returns the U and V grids.

        Defaults to `True`
    stencil_width: int, optional
        Width of the stencil used to compute derivatives. As we use C-grids, it should be an even integer.

        Defaults to ``STENCIL_WIDTH``

    Returns
    -------
    u_geos_u : Float[Array, "lat lon"]
        U component of the geostrophic SSC velocity field (on the U grid)
    v_geos_v : Float[Array, "lat lon"]
        V component of the geostrophic SSC velocity field (on the V grid)
    lat_u : Float[Array, "lat lon"]
        Latitudes of the U grid, if ``return_grids=True``
    lon_u : Float[Array, "lat lon"]
        Longitudes of the U grid, if ``return_grids=True``
    lat_v : Float[Array, "lat lon"]
        Latitudes of the V grid, if ``return_grids=True``
    lon_v : Float[Array, "lat lon"]
        Longitudes of the V grid, if ``return_grids=True``
    """
    if (stencil_width % 2) != 0:
        raise ValueError("stencil_width should an even integer")

    # Make sure the mask is initialized
    is_land = sanitize.init_land_mask(ssh_t, mask)

    # Compute stencil weights
    ssh_t = sanitize.sanitize_data(ssh_t, jnp.nan, is_land)  # avoid spurious velocities near the coast
    stencil_weights = stencil.compute_stencil_weights(ssh_t, lat_t, lon_t, stencil_width=stencil_width)

    # Compute Coriolis factors
    coriolis_factor_t = geometry.compute_coriolis_factor(lat_t)
    coriolis_factor_t = sanitize.sanitize_data(coriolis_factor_t, jnp.nan, is_land)

    u_geos_u, v_geos_v = _geostrophy(ssh_t, stencil_weights, coriolis_factor_t)

    # Handle masked data
    u_geos_u = sanitize.sanitize_data(u_geos_u, jnp.nan, is_land)
    v_geos_v = sanitize.sanitize_data(v_geos_v, jnp.nan, is_land)

    # Compute U and V grids
    lat_u, lon_u, lat_v, lon_v = geometry.compute_uv_grids(lat_t, lon_t)

    res = (u_geos_u, v_geos_v)
    if return_grids:
        res = res + (lat_u, lon_u, lat_v, lon_v)

    return res


@jit
def _geostrophy(
        ssh_t: Float[Array, "lat lon"],
        stencil_weights: Float[Array, "2 2 lat lon stencil_width"],
        coriolis_factor_t: Float[Array, "lat lon"]
) -> [Float[Array, "lat lon"], Float[Array, "lat lon"]]:
    # Compute the gradient of the ssh
    ssh_dx_u = operators.derivative(ssh_t, stencil_weights, axis=1, pad_left=False)  # (T(i), T(i+1)) -> U(i)
    ssh_dy_v = operators.derivative(ssh_t, stencil_weights, axis=0, pad_left=False)  # (T(j), T(j+1)) -> V(j)

    # Interpolate the data
    ssh_dy_t = operators.interpolation(ssh_dy_v, axis=0, pad_left=True)  # (V(j), V(j+1)) -> T(j+1)
    ssh_dy_u = operators.interpolation(ssh_dy_t, axis=1, pad_left=False)  # (T(i), T(i+1)) -> U(i)

    ssh_dx_t = operators.interpolation(ssh_dx_u, axis=1, pad_left=True)  # (U(i), U(i+1)) -> T(i+1)
    ssh_dx_v = operators.interpolation(ssh_dx_t, axis=0, pad_left=False)  # (T(j), T(j+1)) -> V(j)

    coriolis_factor_u = operators.interpolation(coriolis_factor_t, axis=1, pad_left=False)  # (T(i), T(i+1)) -> U(i)
    coriolis_factor_v = operators.interpolation(coriolis_factor_t, axis=0, pad_left=False)  # (T(j), T(j+1)) -> V(j)

    # Computing the geostrophic velocities
    u_geos_u = - geometry.GRAVITY * ssh_dy_u / coriolis_factor_u  # U(i)
    v_geos_v = geometry.GRAVITY * ssh_dx_v / coriolis_factor_v  # V(j)

    return u_geos_u, v_geos_v
