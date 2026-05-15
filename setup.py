from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="vcl_finance",
    version="0.1.0",
    description="VCL Finance — petty cash & finance workflows for ERPNext",
    author="Vimit Converters Limited",
    author_email="tanuj.haria@vimit.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
