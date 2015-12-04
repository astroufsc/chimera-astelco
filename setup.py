from distutils.core import setup

setup(
    name='chimera_astelco',
    version='0.0.1',
    packages=['chimera_astelco', 'chimera_astelco.instruments'],
    scripts=['scripts/chimera-astelcopm'],
    url='https://github.com/astroufsc/chimera-astelco',
    license='GPL v2',
    author='Tiago Ribeiro',
    author_email='tribeiro@ufs.br',
    description='Chimera pluging for ASTELCO system.'
)
