Changelog (nionswift-experimental)
==================================

0.7.18 (2025-07-14)
-------------------
- Fix regression in apply shifts computation.

0.7.17 (2025-06-02)
-------------------
- Maintenance release.

0.7.16 (2025-04-23)
-------------------
- Package dependencies updated.
- Modify computations to use auto-titling.

0.7.15 (2025-01-06)
-------------------
- Update to use latest nionswift dependency.
- Wizard dialog improvements.

0.7.14 (2024-10-27)
-------------------
- Wizard dialog improvements.
- Computation performance improvements.
- Require Numpy 2. Add Python 3.13 support. Drop Python 3.9, 3.10 support.

0.7.13 (2024-06-14)
-------------------
- Directly use selected display item. Fixes #35.
- Make AlignImageSequence copy input metadata dict to result.
- Add flip option to IDPC computation.
- Add new async wizard implementation.

0.7.12 (2023-08-17)
-------------------
- Add typing support.

0.7.10 (2023-08-17)
-------------------
- Add Python 3.11 support. Drop Python 3.8.
- Add crop to valid area functionality.
- Remove measure ZLP; functionality moved to eels-analysis.
- Add find local maxima computation.
- Use multi-d processing base functions from nion.data.

0.7.9 (2022-07-26)
------------------
- Add I vs E square plot computation.
- Fix multi-dimensional crop computation axis choice.

0.7.8 (2022-05-28)
------------------
- Change defaults for SI align.
- Add image sequence align computation.
- Add align SI sequence computation.
- Fix threading problem in measure shifts.

0.7.7 (2022-04-20)
------------------
- Add drift corrector to experimental.
- Use multithreading for measure shifts.
- Minor error improvements during measure shifts.

0.7.6 (2022-02-18)
------------------
- Clean up computations and associated UI.

0.7.5 (2021-12-13)
------------------
- Add option for relative shifts and limited shift range to Multi-D measure shifts.
- Python 3.10 compatibility.

0.7.4 (2021-10-24)
------------------
- Add wizard base classes.
- Add computation for visualizing tableau data.
- Add more fine-grained axis control for multi-dimensional integrate.

0.7.3 (2021-10-20)
------------------
- Release skipped.

0.7.2 (2021-06-16)
------------------
- Fix problem with some axis + shape combinations in MultiDimensionalProcessing.
- Add computations for aligning virtual detector images and combining COM data into a color image.
- Rework DoubleGaussian to use new-style computations and and improved UI.
- Add affine transformation computation.
- Add computation for making an iDPC image out of DPC data.

0.7.1 (2020-11-13)
------------------
- Make join/split preserve display layers.
- Utilize improved tracking of 4D back-graphics.

0.7.0 (2020-08-31)
------------------
- Fix issue with "Select map graphics" buttons in 4D tools.
- Reconnect 4D tools pick graphics after a Swift restart.

0.6.0 (2019-10-24)
------------------
- Add 4D tools (menu items and panel).
- Use eels_analysis library for ZLP amplitude position.

0.5.2 (2019-04-29)
------------------
- Fix issue with mark 0eV being allowed to operate on 2D data.

0.5.1 (2019-01-14)
------------------
- Fix issue with measure ZLP being allowed to operate on 2D data.

0.5.0 (2018-12-12)
------------------
- Nion Swift 0.14 compatibility.
- Align ZLP script.
- Find interface script.

0.4.0 (2018-10-03)
------------------
- Add Multi EELS script.

0.3.1 (2018-05-14)
------------------
- Initial version online.
